"""Explicit Linux receive-only TTY capture into the existing Link outbox.

No polling, TX, DTR/RTS commands, auto-discovery, reconnect or driver loading.
Opening/configuring a USB serial device may affect its hardware control lines;
a reviewed RX-only interface is required. PTYs are explicitly synthetic tests.
"""
from __future__ import annotations

import os
from pathlib import Path
import select
import stat
import sys
import time

from . import link_contract as c
from .link_runtime import DeviceRuntime
from .link_store import safe_path


def acquisition_limits(*, duration_s=10.0, max_chunks=64, max_bytes=8192,
                       max_records=None, idle_timeout_s=None, cancelled=None):
    """Validate host-selected bounds, never instructions obtained from bytes.

    Records count CRLF delimiters, not accepted readings. None preserves the
    original byte/chunk-only transport for callers of the shared receiver.
    """
    c.integer(max_chunks, minimum=1, maximum=128)
    c.integer(max_bytes, minimum=1, maximum=8192)
    if not 0 < c.finite(duration_s) <= 3600:
        raise ValueError("Require a finite duration in (0, 3600]")
    if max_records is not None:
        c.integer(max_records, minimum=1, maximum=32)
    if idle_timeout_s is not None and not 0 < c.finite(idle_timeout_s) <= 3600:
        raise ValueError("Require a finite idle timeout in (0, 3600]")
    if cancelled is not None and not callable(cancelled):
        raise ValueError("Cancellation must be a host callback, not device data")
    return dict(duration_s=duration_s, max_chunks=max_chunks, max_bytes=max_bytes,
                max_records=max_records, idle_timeout_s=idle_timeout_s)


def capture_serial(path, *, device, journal, authority, channel_id,
                   baud=9600, duration_s=10.0, max_chunks=64, max_bytes=8192,
                   allow_pty=False, context=None, procedure_ref=None,
                   max_records=None, idle_timeout_s=None, cancelled=None):
    """Capture one finite session; committed prefix survives disconnect/errors.

    Bounds are not a promise that every instrument sample was acquired. A process
    crash between kernel read and SQLite commit can lose in-flight bytes. No ACK
    is sent to the instrument and no physical exactly-once claim is made.
    """
    device=c.manifest(device)
    authority.check("READ",device,time.time_ns())
    if sys.platform != "linux":
        raise ValueError("This receive-only driver is qualified in software on Linux only")
    import fcntl
    import termios
    channel=c.channel_for(device,channel_id)
    if (channel["encoding"]!="opaque.v1" or device["clock"]["id"]!="link.host.monotonic_ns"
            or device["clock"]["tick_hz"]!=1000000000
            or device["clock"]["timestamp_event"]!="instrument_report_receipt"
            or device["clock"]["sync_reference"] is not None
            or device["clock"]["uncertainty_ns"] is not None):
        raise ValueError("Require opaque raw capture and unqualified host-monotonic receipt clock")
    if type(baud) is not int or baud not in (1200,2400,4800,9600,19200,38400,57600,115200):
        raise ValueError("Unsupported explicitly selected baud")
    bounds = acquisition_limits(duration_s=duration_s, max_chunks=max_chunks, max_bytes=max_bytes,
        max_records=max_records, idle_timeout_s=idle_timeout_s, cancelled=cancelled)
    if type(allow_pty) is not bool:
        raise ValueError("Invalid finite capture bounds")
    if context is not None:
        c.keys(context, c.CONTEXT_FIELDS)
        for item in context.values():
            if item is not None: c.text(item)
    if procedure_ref is not None: c.content_ref(procedure_ref)
    original=Path(path)
    if not original.is_absolute(): raise ValueError("Bind an absolute operator-selected TTY path")
    path=safe_path(original)
    before=os.stat(path,follow_symlinks=False)
    is_pty=str(path).startswith("/dev/pts/")
    if not stat.S_ISCHR(before.st_mode) or (is_pty and not allow_pty):
        raise ValueError("Require a character device; PTY tests require explicit permission")
    authority.check("READ",device,time.time_ns())
    fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOCTTY|os.O_NOFOLLOW)
    old=None; runtime=None; count=total=0; status="DURATION_BOUND_REACHED"; result=None; exclusive=False
    try:
        actual=os.fstat(fd)
        if (before.st_dev,before.st_ino,before.st_rdev)!=(actual.st_dev,actual.st_ino,actual.st_rdev) or not os.isatty(fd):
            raise ValueError("TTY identity changed or descriptor is not a terminal")
        fcntl.ioctl(fd,termios.TIOCEXCL)
        exclusive=True
        old=termios.tcgetattr(fd)
        attrs=list(old);attrs[6]=list(old[6])
        attrs[0]=0;attrs[1]=0;attrs[2]=termios.CS8|termios.CREAD|termios.CLOCAL;attrs[3]=0
        attrs[4]=attrs[5]=getattr(termios,f"B{baud}")
        attrs[6][termios.VMIN]=0;attrs[6][termios.VTIME]=0
        # TCSANOW deliberately avoids flushing unread bytes.
        authority.check("READ",device,time.time_ns())
        termios.tcsetattr(fd,termios.TCSANOW,attrs)
        runtime=DeviceRuntime(device,journal,origin="synthetic" if is_pty else "recorded")
        runtime.event("recording_started",f"linux receive-only 8N1 baud={baud}; no gauge commands",tick=time.monotonic_ns())
        last_received=time.monotonic()
        deadline=last_received+duration_s
        records=0; previous=b""
        while count<max_chunks and total<max_bytes:
            authority.check("READ",device,time.time_ns())
            try:
                if cancelled is not None:
                    stop=cancelled()
                    if type(stop) is not bool:
                        raise ValueError("Cancellation callback must return a boolean")
                    if stop:
                        status="CANCELLED"; break
                now=time.monotonic()
                remaining=deadline-now
                if remaining<=0: break
                if idle_timeout_s is not None:
                    idle_remaining=last_received+idle_timeout_s-now
                    if idle_remaining<=0:
                        status="IDLE_TIMEOUT"; break
                    remaining=min(remaining,idle_remaining)
                ready,_,_=select.select([fd],[],[],min(remaining,0.1))
                if not ready: continue
                size=min(256,max_bytes-total)
                if max_records is not None:
                    # Even a stream of CRLF pairs cannot cross the record bound.
                    # Never read a suffix only to discard it after interpretation.
                    size=min(size,2*(max_records-records)-(previous==b"\r"))
            except KeyboardInterrupt:
                status="CANCELLED"; break
            except BlockingIOError: continue
            except OSError as exc:
                status="DISCONNECTED_OR_READ_ERROR"
                runtime.event("disconnect",f"TTY read errno={exc.errno}; instrument completeness unknown",tick=time.monotonic_ns())
                break
            # Readiness waiting can outlive a short grant. This check stays
            # outside the transport OSError handler: denial is not a read fault.
            authority.check("READ",device,time.time_ns())
            try:
                raw=os.read(fd,size)
            except KeyboardInterrupt:
                status="CANCELLED"; break
            except BlockingIOError: continue
            except OSError as exc:
                status="DISCONNECTED_OR_READ_ERROR"
                runtime.event("disconnect",f"TTY read errno={exc.errno}; instrument completeness unknown",tick=time.monotonic_ns())
                break
            if not raw:
                status="DISCONNECTED_OR_EOF"
                runtime.event("disconnect","TTY EOF; instrument completeness unknown",tick=time.monotonic_ns());break
            # Preserve every received chunk before interpretation. Never assign
            # host receipt wall time to acquired_at or a sensor sample clock.
            runtime.emit(channel_id,raw,tick=time.monotonic_ns(),context=context,procedure_ref=procedure_ref)
            count+=1;total+=len(raw)
            last_received=time.monotonic()
            records+=(previous+raw).count(b"\r\n")
            previous=raw[-1:]
            if max_records is not None and records>=max_records:
                status="RECORD_BOUND_REACHED"; break
        else: status="SIZE_BOUND_REACHED"
        runtime.event("recording_stopped",status+"; complete instrument stream not established",tick=time.monotonic_ns())
        result = {"schema":"ciw.link-serial-receipt.v1","status":status,"boot_id":runtime.boot_id,
            "manifest_ref":c.content_identity(device),"journal_id":journal.store_id,"chunks":count,"bytes":total,
            "origin":"synthetic" if is_pty else "recorded","transport":"linux_tty_8n1_rx_only",
            "device_node":{"device":actual.st_dev,"inode":actual.st_ino,"rdev":actual.st_rdev},
            "clock":"host_monotonic_chunk_receipt_not_sensor_sample",
            "completeness":"not_established","bytes_transmitted":0,"limits":dict(c.LIMITS),
            "acquisition":{**bounds,"records":records}}
        return result
    except termios.error as exc:
        raise OSError("TTY configuration failed: " + str(exc)) from exc
    finally:
        if old is not None:
            try:
                termios.tcsetattr(fd,termios.TCSANOW,old)
                if result is not None: result["terminal_settings_restore"] = "RESTORED"
            except (OSError, termios.error):
                if result is not None: result["terminal_settings_restore"] = "FAILED_OR_DISCONNECTED"
        if exclusive:
            try: fcntl.ioctl(fd,termios.TIOCNXCL)
            except OSError: pass
        os.close(fd)
