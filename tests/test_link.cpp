#include "ebrush_link.h"
#include "s3_adaptive.h"
#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <limits>

using namespace EBrushLink;

void check_packet(size_t count) {
  Sample input[MAX_SAMPLES]{};
  for (size_t i = 0; i < count; ++i) {
    input[i].frame_id = 100 + i;
    input[i].sensor_time_us32 = 0xffff0000 + i * 5000;
    input[i].hx = static_cast<int16_t>(-3000 + static_cast<int>(i));
  }
  uint8_t wire[MAX_PACKET];
  size_t n = make_packet(TYPE_SAMPLES, 52, input, sizeof(Sample)*count,
                         static_cast<uint8_t>(count), wire, sizeof(wire));
  assert(n == sizeof(Header) + sizeof(Sample)*count + 2);
  Header h{};
  const uint8_t* p = nullptr;
  assert(parse_packet(wire,n,h,p));
  assert(h.bundle_seq == 52 && h.count == count && h.type == TYPE_SAMPLES);
  assert(memcmp(p,input,sizeof(Sample)*count)==0);
  assert(!parse_packet(wire,n-1,h,p));
  wire[n-1] ^= 0x40;
  assert(!parse_packet(wire,n,h,p));
  wire[n-1] ^= 0x40;
  wire[4] ^= 0x01;
  assert(!parse_packet(wire,n,h,p));
}

int main() {
  for (size_t count=1; count <= MAX_SAMPLES; ++count) check_packet(count);
  Sample s{};
  uint8_t buffer[MAX_PACKET];
  assert(make_packet(TYPE_SAMPLES,0,&s,sizeof(s),0,buffer,sizeof(buffer))==0);
  assert(make_packet(TYPE_SAMPLES,0,&s,sizeof(s),5,buffer,sizeof(buffer))==0);
  assert(make_packet(TYPE_SAMPLES,0,&s,sizeof(s),1,buffer,10)==0);
  assert(make_packet(TYPE_SAMPLES,0,nullptr,sizeof(s),1,buffer,sizeof(buffer))==0);
  assert(make_packet(255,0,&s,sizeof(s),1,buffer,sizeof(buffer))==0);
  Meta m{};
  m.boot_id=42;
  size_t n = make_packet(TYPE_META,7,&m,sizeof(m),1,buffer,sizeof(buffer));
  Header h{};
  const uint8_t* payload = nullptr;
  assert(parse_packet(buffer,n,h,payload));
  assert(reinterpret_cast<const Meta*>(payload)->boot_id==42);
  buffer[0]=0;
  assert(!parse_packet(buffer,n,h,payload));

  // Each flipped bit of a four-sample packet must fail end-to-end CRC16.
  Sample fs[MAX_SAMPLES]{};
  uint8_t fuzz_wire[MAX_PACKET];
  const size_t fuzz_len = make_packet(TYPE_SAMPLES, 0xfffffffdu, fs,
      sizeof(fs), MAX_SAMPLES, fuzz_wire, sizeof(fuzz_wire));
  assert(fuzz_len > 0);
  for (size_t i = 0; i < fuzz_len; ++i) {
    for (uint8_t bit = 0; bit < 8; ++bit) {
      fuzz_wire[i] ^= static_cast<uint8_t>(1u << bit);
      assert(!parse_packet(fuzz_wire, fuzz_len, h, payload));
      fuzz_wire[i] ^= static_cast<uint8_t>(1u << bit);
    }
  }
  assert(parse_packet(fuzz_wire, fuzz_len, h, payload));

  // Strict monotonic rollover across the 32-bit microsecond boundary.
  TickUnwrapper clock;
  uint64_t a=0,b=0,c=0;
  assert(clock.unwrap(0xfffff000,a));
  assert(clock.unwrap(0xfffff500,b));
  assert(clock.unwrap(0x00000600,c));
  assert(b-a==0x500);
  assert(c-b==0x1100);
  assert(!clock.unwrap(0x000005ff,c));
  assert(clock.unwrap(0x00000700,c));
  assert(c-b==0x1200);
  clock.reset();
  assert(clock.unwrap(123,a) && a==123);

  Adaptive::Filter filter;
  const float hall[3]={0.1f,0.2f,0.3f};
  const float still[3]={0.f,0.f,1.f};
  for(int i=0;i<40;++i) {
    auto out = filter.update(hall,true,still,true,.005f,0);
    assert(std::isfinite(out.confidence));
    assert(out.confidence>=0 && out.confidence<=1);
    assert(out.motion_energy < .001f);
  }
  const float moving[3]={.8f,0.f,1.f};
  auto out=filter.update(hall,true,moving,true,.005f,2);
  assert(out.motion_energy > 0);
  assert(out.confidence < 1);
  out=filter.update(hall,false,moving,false,.005f,30);
  assert((out.flags & 7u) == 7u);
  filter.reset();
  out=filter.update(hall,true,still,true,.005f,0);
  assert(out.motion_energy == 0);
  printf("OK: LINK packet 1-4 batch/meta/CRC/rollover + adaptive\n");
  return 0;
}
