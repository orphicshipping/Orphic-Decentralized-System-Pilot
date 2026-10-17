/* AGPL-3.0-or-later. Portable unauthenticated Link v1 framing reference.
 * No board drivers, cryptographic substitute, command dispatch, or allocation.
 */
#ifndef NOTATION_LINK_WIRE_H
#define NOTATION_LINK_WIRE_H
#include <stddef.h>
#include <stdint.h>
#define NL_HEADER 12u
#define NL_MAX_PAYLOAD 65536u
#define NL_MAX_WIRE (NL_HEADER + NL_MAX_PAYLOAD + 4u)
uint32_t nl_crc32(const uint8_t *data, size_t length);
int nl_encode(unsigned kind, const uint8_t *payload, size_t length,
              uint8_t *out, size_t capacity, size_t *written);
int nl_decode(const uint8_t *wire, size_t length, unsigned *kind,
              const uint8_t **payload, size_t *payload_length);
#endif
