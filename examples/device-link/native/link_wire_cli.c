/* Host oracle for the portable firmware-facing codec. Not a physical producer. */
#include "link_wire.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif
static uint8_t input[NL_MAX_WIRE + 1u];
static uint8_t output[NL_MAX_WIRE];
int main(int argc, char **argv) {
    size_t n, written = 0u;
    const uint8_t *payload = NULL;
    unsigned kind = 0u;
    int result;
#ifdef _WIN32
    _setmode(_fileno(stdin), _O_BINARY); _setmode(_fileno(stdout), _O_BINARY);
#endif
    n = fread(input, 1u, sizeof(input), stdin);
    if (ferror(stdin) || n == sizeof(input)) return 2;
    if (argc == 3 && strcmp(argv[1], "encode") == 0) {
        char *end;
        long value = strtol(argv[2], &end, 10);
        if (*end || value < 1 || value > 3) return 2;
        result = nl_encode((unsigned)value, input, n, output, sizeof(output), &written);
        if (result) return 2;
        return fwrite(output, 1u, written, stdout) == written ? 0 : 2;
    }
    if (argc == 2 && strcmp(argv[1], "decode") == 0) {
        result = nl_decode(input, n, &kind, &payload, &written);
        if (result) return 2;
        return fwrite(payload, 1u, written, stdout) == written ? 0 : 2;
    }
    return 2;
}
