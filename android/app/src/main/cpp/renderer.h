#pragma once
#include <android/asset_manager.h>
#include <android/native_window.h>
#include <android/log.h>
#include <array>
#include <cstdint>
#include <memory>
#include <string>
#include <stdexcept>
#include <vector>
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO,"AC8Terrain",__VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_WARN,"AC8Terrain",__VA_ARGS__)
constexpr int bands=56, rows=64, block=2048, hop=1024;
struct Params {
    float view[4],motion[4],screen[4],history[4],geometry[4];
    int instances() const {
        const int columns=static_cast<int>(geometry[0]);
        return 64*16+columns*rows*2+(motion[3]>.5f?rows:columns);
    }
};
constexpr int maxBlocks=rows/2;
using Samples=std::array<uint32_t,maxBlocks*block+hop>;
class Renderer {
public:
    virtual ~Renderer()=default;
    // 1 requests surface recreation; other failures advance to the next backend.
    virtual int draw(const Params&,int hops)=0;
    Samples samples{};
    std::array<uint32_t,hop> overlap{};
    int row=0,valid=0;
};
std::string asset(AAssetManager*,const std::string&);
std::unique_ptr<Renderer> makeGL(ANativeWindow*,AAssetManager*);
