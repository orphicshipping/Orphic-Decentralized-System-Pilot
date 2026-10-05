# Notation Link reference producer

Run `net link demo --profile GaugeLink --output-dir results/link-001` after
installing this branch. All seven profile names use the same strict schema.
See [Device Link](../../docs/DEVICE_LINK.md) for the complete operating contract.

## Native unsigned wire codec

```sh
cc -std=c11 -Wall -Wextra -Wpedantic -Werror -O2 native/link_wire.c native/link_wire_cli.c -o link-wire
printf '{"example":true}' | ./link-wire encode 3 > event.link
./link-wire decode < event.link
```

Run from this directory. This example only checks framing of arbitrary bytes.
It is not a valid NET event until it also satisfies the strict event contract.
The caller owns fixed input/output buffers. The codec is independent of Python
and uses no heap allocation. It detects length/CRC errors and refuses unknown
flags, concatenated frames and MAC frames. A host comparison suite tests every
wire byte against the Python codec, including maximum-sized payloads.

**No target board, UART pin, USB stack, MCU SDK, FPGA part, HDL bitstream, ADC,
AFE, meter protocol, electrical rating or physical timing accuracy is selected.**
This C11 source is a portable starting point for a later board port, not a
flashed or production-qualified instrument firmware. HMAC exists in the host
reference protocol; secure board key storage and MCU cryptography remain gates.
