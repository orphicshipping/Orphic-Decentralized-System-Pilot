"""Private bounded read-only protocol worker; never a model-selected program.

SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import importlib.metadata
import json
import os
import ssl
import sys
import time


def instant(value=None):
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def mqtt_read(settings, secrets, count, seconds):
    import paho.mqtt.client as mqtt
    from paho.mqtt.packettypes import PacketTypes
    from paho.mqtt.properties import Properties
    rows, failure = [], []
    handshake = {'connected': False, 'subscription_mid': None, 'subscribed': False}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5,
                         reconnect_on_failure=False)
    client.connect_timeout = min(seconds, 5)
    if settings['tls']:
        context = ssl.create_default_context(cafile=settings.get('ca_file'))
        if secrets.get('certificate'):
            context.load_cert_chain(secrets['certificate'], secrets['private_key'])
        client.tls_set_context(context)
    if secrets.get('username'):
        client.username_pw_set(secrets['username'], secrets.get('password'))
    def connected(c, _userdata, _flags, reason, _properties):
        if reason.is_failure:
            failure.append('mqtt_connection_refused')
        else:
            handshake['connected'] = True
            code, mid = c.subscribe(settings['topic'], qos=settings['qos'])
            if code != mqtt.MQTT_ERR_SUCCESS:
                failure.append('mqtt_subscription_refused')
            else:
                handshake['subscription_mid'] = mid
    def subscribed(_c, _userdata, mid, reasons, _properties):
        if (not handshake['connected'] or mid != handshake['subscription_mid'] or
                len(reasons) != 1 or reasons[0].is_failure or
                reasons[0].value not in range(settings['qos'] + 1)):
            failure.append('mqtt_subscription_refused')
        else:
            handshake['subscribed'] = True
    def received(_c, _userdata, message):
        if len(rows) >= count:
            return
        if message.topic != settings['topic'] or len(message.payload) > 4096:
            failure.append('mqtt_message_refused')
            return
        rows.append({'received_at': instant(), 'observation': {
            'message_type': 'observation', 'topic': message.topic, 'qos': message.qos,
            'retain': bool(message.retain), 'duplicate': bool(message.dup),
            'payload_b64': base64.b64encode(message.payload).decode('ascii')}})
    client.on_connect, client.on_message, client.on_subscribe = connected, received, subscribed
    properties = Properties(PacketTypes.CONNECT)
    properties.MaximumPacketSize = 8192
    properties.ReceiveMaximum = min(count, 32)
    deadline = time.monotonic() + seconds
    try:
        client.connect(settings['host'], settings['port'], keepalive=15,
                       clean_start=True, properties=properties)
        while (not handshake['subscribed'] or len(rows) < count) and not failure and time.monotonic() < deadline:
            rc = client.loop(timeout=min(0.1, max(0.001, deadline-time.monotonic())))
            if rc != mqtt.MQTT_ERR_SUCCESS:
                failure.append('mqtt_connection_lost')
        if failure:
            raise ValueError(failure[0])
        if not handshake['connected'] or not handshake['subscribed']:
            raise ValueError('mqtt_handshake_incomplete')
        return rows
    finally:
        client.disconnect()


async def opcua_read(settings, secrets, count, seconds):
    from asyncua import Client, ua
    from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256
    client = Client(settings['endpoint'], timeout=min(seconds, 5))
    client.application_uri = settings['application_uri']
    if settings['secure']:
        await client.set_security(SecurityPolicyBasic256Sha256, secrets['certificate'],
            secrets['private_key'], server_certificate=settings['server_certificate'],
            mode=ua.MessageSecurityMode.SignAndEncrypt)
    if secrets.get('username'):
        client.set_user(secrets['username'])
        client.set_password(secrets['password'])
    rows = []
    connected = False
    deadline = asyncio.timeout(seconds)
    try:
        async with deadline:
            async with client:
                connected = True
                node = client.get_node(settings['node_id'])
                for _ in range(count):
                    value = await node.read_data_value(raise_on_bad_status=False)
                    variant = value.Value
                    native_type = variant.VariantType.name if variant is not None else 'Null'
                    unavailable = (variant is None or variant.Value is None) and value.StatusCode.value >> 30 in (1,2,3)
                    datatype = settings['datatype'] if unavailable and native_type == 'Null' else native_type
                    if datatype != settings['datatype']:
                        raise ValueError('opcua_datatype_changed')
                    rows.append({'received_at': instant(), 'native_datatype': native_type, 'observation': {
                        'node_id': settings['node_id'], 'datatype': datatype,
                        'value': variant.Value if variant is not None else None, 'unit': settings['unit'],
                        'status_code': value.StatusCode.value,
                        'source_timestamp': instant(value.SourceTimestamp) if value.SourceTimestamp else None,
                        'server_timestamp': instant(value.ServerTimestamp) if value.ServerTimestamp else None}})
                    if len(rows) < count:
                        await asyncio.sleep(settings['interval_seconds'])
    except TimeoutError:
        # Only the caller's elapsed observation window permits partial output.
        # Connection failures, SDK request timeouts and security errors refuse.
        if not deadline.expired() or not connected:
            raise
    return rows


def ros2_read(settings, secrets, count, seconds):
    # The host supplies an installed ROS2 environment, including its security
    # keystore for a live binding. The worker does not discover or modify it.
    if settings['secure'] and (os.environ.get('ROS_SECURITY_ENABLE') != 'true' or
            os.environ.get('ROS_SECURITY_STRATEGY') != 'Enforce' or
            os.environ.get('ROS_SECURITY_KEYSTORE') != settings['security_keystore']):
        raise ValueError('ros2_security_environment_missing')
    import rclpy
    from rclpy.context import Context
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy
    from sensor_msgs.msg import JointState
    context = Context()
    rclpy.init(args=[], context=context, domain_id=settings['domain_id'])
    node = None
    rows, failure = [], []
    try:
        node = Node('odsp_read_' + str(os.getpid()), context=context, use_global_arguments=False,
                    enable_rosout=False, start_parameter_services=False)
        qos = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST,
            reliability=ReliabilityPolicy.RELIABLE if settings['reliable'] else ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE)
        def received(message):
            if len(rows) >= count:
                return
            if len(message.name) > 64 or set(message.name) != set(settings['joints']):
                failure.append('ros2_joint_binding_changed')
                return
            rows.append({'received_at': instant(), 'observation': {
                'stamp': {'sec': message.header.stamp.sec, 'nanosec': message.header.stamp.nanosec},
                'clock': settings['clock'], 'frame_id': message.header.frame_id,
                'name': list(message.name), 'joint_type': [settings['joints'][name] for name in message.name],
                'position': list(message.position), 'velocity': list(message.velocity),
                'effort': list(message.effort)}})
        subscription = node.create_subscription(JointState, settings['topic'], received, qos)
        deadline = time.monotonic()+seconds
        while len(rows)<count and not failure and time.monotonic()<deadline:
            rclpy.spin_once(node, timeout_sec=min(0.1, max(0., deadline-time.monotonic())), context=context)
        if failure:
            raise ValueError(failure[0])
        node.destroy_subscription(subscription)
        return rows
    finally:
        if node is not None:
            node.destroy_node()
        context.try_shutdown()


def main():
    # Resource limits are mandatory in the parent launch. These byte limits
    # additionally bound the private protocol; no response contains credentials.
    raw = sys.stdin.buffer.read(65537)
    if len(raw)>65536:
        raise ValueError('worker_input_budget')
    request = json.loads(raw)
    protocol = request['protocol']
    sdk = {'mqtt.scalar-observation.v1': 'paho-mqtt', 'opcua.scalar.v1': 'asyncua',
           'ros2.joint-state.v1': 'rclpy'}[protocol]
    if sdk != 'rclpy':
        version = importlib.metadata.version(sdk)
    else:
        from ament_index_python.packages import get_package_share_directory
        import xml.etree.ElementTree as ET
        from pathlib import Path
        version = ET.parse(Path(get_package_share_directory('rclpy'))/'package.xml').findtext('version')
    if version != request['sdk_version']:
        raise ValueError('protocol_sdk_version_changed')
    args = (request['settings'], request['secrets'], request['count'], request['seconds'])
    if protocol == 'opcua.scalar.v1':
        rows = asyncio.run(opcua_read(*args))
    else:
        rows = (mqtt_read if protocol == 'mqtt.scalar-observation.v1' else ros2_read)(*args)
    result = {'schema': 'odsp.live-intake.worker.v1', 'sdk': sdk, 'sdk_version': version,
              'records': rows, 'completed_at': instant()}
    encoded=json.dumps(result,ensure_ascii=True,allow_nan=False,separators=(',', ':')).encode()
    if len(encoded)>262144:
        raise ValueError('worker_output_budget')
    sys.stdout.buffer.write(encoded)


def entrypoint():
    try:
        main()
    except Exception:
        # Library exception strings can contain URLs, identities or credentials.
        sys.stderr.write('live_protocol_read_refused\n')
        raise SystemExit(2)


if __name__ == '__main__':
    entrypoint()
