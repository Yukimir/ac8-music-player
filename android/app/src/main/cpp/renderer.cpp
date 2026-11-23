#include "renderer.h"
#include <android/native_window_jni.h>
#include <android/asset_manager_jni.h>
#include <algorithm>
#include <cstring>
#include <cmath>

std::string asset(AAssetManager* manager,const std::string& path) {
    std::unique_ptr<AAsset,decltype(&AAsset_close)> file(AAssetManager_open(manager,path.c_str(),AASSET_MODE_BUFFER),AAsset_close);
    if(!file) throw std::runtime_error("Missing shader "+path);
    return std::string(static_cast<const char*>(AAsset_getBuffer(file.get())), AAsset_getLength(file.get()));
}
extern "C" JNIEXPORT jlong JNICALL Java_local_ac8_mobile_TerrainGpu_openGL(
    JNIEnv* env,jobject,jobject surface,jobject assets) {
    std::unique_ptr<ANativeWindow,decltype(&ANativeWindow_release)> w(ANativeWindow_fromSurface(env,surface),ANativeWindow_release);
    if(!w)return 0;
    try {
        auto m=AAssetManager_fromJava(env,assets);
        auto r=makeGL(w.get(),m);
        return reinterpret_cast<jlong>(r.release());
    } catch(const std::exception& e){LOGE("OpenGL ES: %s",e.what());return 0;}
}
extern "C" JNIEXPORT jint JNICALL Java_local_ac8_mobile_TerrainGpu_draw(
    JNIEnv* env,jobject,jlong handle,jfloatArray values,jobjectArray audio,jint count,jboolean clear) {
    auto& r=*reinterpret_cast<Renderer*>(handle);
    Params p{};
    env->GetFloatArrayRegion(values,0,16,reinterpret_cast<float*>(&p));
    // Widest visible row is the far edge. Near rows cover fewer columns.
    const float cell=p.motion[0]/(.0293f*p.screen[3]*(1.f-p.motion[2]));
    const float slope=p.view[3]-p.view[2];
    const float turn=-slope*cell/(p.view[2]+slope*p.view[0]-p.view[1]);
    auto column=[&](float dx){return dx/(cell-dx*turn);};
    p.geometry[0]=std::clamp(std::ceil(column(1.f-p.view[0])-column(-p.view[0]))+4.f,4.f,160.f);
    if(clear){r.row=r.valid=0;r.overlap.fill(0);}
    if(count<0 || count>maxBlocks || count>env->GetArrayLength(audio))return -1;
    if(count){
        std::copy(r.overlap.begin(),r.overlap.end(),r.samples.begin());
        for(int i=0;i<count;i++){
            auto pcm=static_cast<jbyteArray>(env->GetObjectArrayElement(audio,i));
            if(!pcm || env->GetArrayLength(pcm)!=block*4)return -1;
            env->GetByteArrayRegion(pcm,0,block*4,reinterpret_cast<jbyte*>(r.samples.data()+hop+i*block));
            env->DeleteLocalRef(pcm);
        }
        std::copy_n(r.samples.begin()+count*block,hop,r.overlap.begin());
        r.row=(r.row+count*2)%rows;r.valid=std::min(rows,r.valid+count*2);
    }
    p.history[0]=float(r.row);p.history[1]=float(r.valid);
    try {return r.draw(p,count*2);}
    catch(const std::exception& e){LOGE("Frame: %s",e.what());return -1;}
}
extern "C" JNIEXPORT void JNICALL Java_local_ac8_mobile_TerrainGpu_close(JNIEnv*,jobject,jlong handle){
    delete reinterpret_cast<Renderer*>(handle);
}
