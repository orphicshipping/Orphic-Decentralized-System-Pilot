// Independent native reference for the DBI1/DBO1 scalar wire contract.
// No device IO, calibration trust, proof, or execution authority is supplied.
#include <cstdint>
#include <cstring>
#include <iostream>
#include <iterator>
#include <vector>
#ifdef _WIN32
#include <io.h>
#include <fcntl.h>
#endif

static uint64_t u64(const std::vector<unsigned char>& b, size_t i) {
    uint64_t v=0; for(unsigned j=0;j<8;++j) v|=uint64_t(b[i+j])<<(8*j); return v;
}
static int64_t i64(const std::vector<unsigned char>& b, size_t i) {
    uint64_t v=u64(b,i); int64_t x; std::memcpy(&x,&v,8); return x;
}
int main() {
#ifdef _WIN32
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    std::vector<unsigned char> b;
    char c;
    while(std::cin.get(c)) { b.push_back(static_cast<unsigned char>(c)); if(b.size()>68)return 2; }
    if(b.size()!=68 || std::memcmp(b.data(),"DBI1",4)!=0)return 2;
    const int64_t count=i64(b,36), gain=i64(b,44), offset=i64(b,52);
    const uint64_t den=u64(b,60);
    if(count<-(INT64_C(1)<<40)||count>(INT64_C(1)<<40)||gain<-(INT64_C(1)<<20)||gain>(INT64_C(1)<<20))
        return 3;
    if(offset<-(INT64_C(1)<<60)||offset>(INT64_C(1)<<60)||den==0||den>(UINT64_C(1)<<20))return 3;
    // Proven bound: |count*gain+offset| <= 2^61, within signed int64.
    const int64_t y=count*gain+offset;
    std::vector<unsigned char> out={'D','B','O','1'};
    out.insert(out.end(),b.begin()+4,b.begin()+36);
    uint64_t bits;std::memcpy(&bits,&y,8);
    for(unsigned j=0;j<8;++j)out.push_back((bits>>(8*j))&255);
    for(unsigned j=0;j<8;++j)out.push_back(y<0?255:0); // sign-extend to i128
    out.insert(out.end(),b.begin()+60,b.end());
    std::cout.write(reinterpret_cast<const char*>(out.data()),out.size());
    return std::cout.good()?0:4;
}
