#include "link_wire.h"
#include <string.h>

static uint32_t get32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
}
static void put32(uint8_t *p, uint32_t value) {
    p[0] = (uint8_t)(value >> 24); p[1] = (uint8_t)(value >> 16);
    p[2] = (uint8_t)(value >> 8); p[3] = (uint8_t)value;
}
uint32_t nl_crc32(const uint8_t *data, size_t length) {
    uint32_t crc = UINT32_C(0xffffffff);
    size_t i;
    unsigned bit;
    for (i = 0; i < length; ++i) {
        crc ^= data[i];
        for (bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ ((crc & 1u) ? UINT32_C(0xedb88320) : 0u);
    }
    return ~crc;
}
int nl_encode(unsigned kind, const uint8_t *payload, size_t length,
              uint8_t *out, size_t capacity, size_t *written) {
    size_t total;
    if (!payload || !out || !written || kind < 1u || kind > 3u || length == 0u || length > NL_MAX_PAYLOAD)
        return -1;
    total = NL_HEADER + length + 4u;
    if (capacity < total) return -2;
    memcpy(out, "NLK1", 4u); out[4] = 1u; out[5] = 0u; out[6] = 0u; out[7] = (uint8_t)kind;
    put32(out + 8u, (uint32_t)length);
    memcpy(out + NL_HEADER, payload, length);
    put32(out + NL_HEADER + length, nl_crc32(out, NL_HEADER + length));
    *written = total;
    return 0;
}
int nl_decode(const uint8_t *wire, size_t length, unsigned *kind,
              const uint8_t **payload, size_t *payload_length) {
    uint32_t count;
    if (!wire || !kind || !payload || !payload_length || length < NL_HEADER + 4u || length > NL_MAX_WIRE)
        return -1;
    /* HMAC frames must be verified by a cryptographic implementation, not stripped. */
    if (memcmp(wire, "NLK1", 4u) || wire[4] != 1u || wire[5] != 0u || wire[6] != 0u || wire[7] < 1u || wire[7] > 3u)
        return -2;
    count = get32(wire + 8u);
    if (count == 0u || count > NL_MAX_PAYLOAD || length != NL_HEADER + (size_t)count + 4u)
        return -3;
    if (get32(wire + NL_HEADER + count) != nl_crc32(wire, NL_HEADER + count))
        return -4;
    *kind = wire[7]; *payload = wire + NL_HEADER; *payload_length = count;
    return 0;
}
