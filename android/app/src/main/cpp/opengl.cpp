#include "renderer.h"
#include <EGL/egl.h>
#include <GLES3/gl31.h>
#include <algorithm>
#include <cmath>
#include <complex>

namespace {
void replaceAll(std::string& s,const std::string& a,const std::string& b) {
    for(size_t i=0;(i=s.find(a,i))!=s.npos;i+=b.size())s.replace(i,a.size(),b);
}
class GLRenderer final:public Renderer {
    EGLDisplay dpy=EGL_NO_DISPLAY;
    EGLContext context=EGL_NO_CONTEXT;
    EGLSurface surface=EGL_NO_SURFACE;
    GLuint graphics=0,compute=0,buf[3]{},texture=0,vao=0;
    bool legacy=false;
    std::array<std::complex<float>,block> fft{},twiddles{};
    std::array<float,block> window{};
    GLuint program(AAssetManager* a,std::initializer_list<std::pair<GLenum,const char*>> stages) {
        GLuint prog=glCreateProgram();
        try {
            for(auto [kind,file]:stages) {
                std::string src=asset(a,file);
                replaceAll(src,"#include \"terrain.inc\"",asset(a,"terrain.inc"));
                replaceAll(src,"#version 450", legacy?"#version 300 es\n#define GL_LEGACY 1":"#version 310 es");
                for(int loc=0;loc<4;loc++)replaceAll(src,"layout(location="+std::to_string(loc)+") ","");
                auto id=glCreateShader(kind);auto ptr=src.c_str();
                glShaderSource(id,1,&ptr,nullptr);glCompileShader(id);
                GLint ok;glGetShaderiv(id,GL_COMPILE_STATUS,&ok);
                if(!ok){char log[2048]{};glGetShaderInfoLog(id,sizeof(log),nullptr,log);glDeleteShader(id);throw std::runtime_error(std::string(file)+": "+log);}
                glAttachShader(prog,id);glDeleteShader(id);
            }
            glLinkProgram(prog);GLint ok;glGetProgramiv(prog,GL_LINK_STATUS,&ok);
            if(!ok){char log[2048]{};glGetProgramInfoLog(prog,sizeof(log),nullptr,log);throw std::runtime_error(log);}
            glUniformBlockBinding(prog,glGetUniformBlockIndex(prog,"Params"),0);
            return prog;
        }catch(...){glDeleteProgram(prog);throw;}
    }
    // GLES 3.0 has vertex textures but no compute stage. Only that fallback analyzes PCM on the CPU.
    void legacyRow(const uint32_t* pcm,int y) {
        for(int i=0,j=0;i<block;i++){
            const auto bits=pcm[i];
            fft[j]=std::complex<float>(int16_t(bits),int16_t(bits>>16))*(window[i]/32768.f);
            int bit=block>>1;for(;bit && (j&bit);bit>>=1)j^=bit;j^=bit;
        }
        for(int half=1;half<block;half*=2)for(int i=0;i<block;i+=half*2)for(int j=0;j<half;j++){
            auto left=fft[i+j],right=fft[i+j+half]*twiddles[j*block/(2*half)];
            fft[i+j]=left+right;fft[i+j+half]=left-right;
        }
        std::array<float,bands> v{};float maxLevel=0;
        auto bin=[](int j){return int(std::round(38.*std::pow(16500./38.,j/56.)*block/48000.));};
        for(int i=0;i<bands;i++){
            float peak=0;int lo=std::max(1,bin(i)),hi=std::max(lo+1,bin(i+1));
            for(int k=lo;k<hi;k++)peak=std::max(peak,.5f*(std::norm(fft[k])+std::norm(fft[block-k])));
            v[i]=std::clamp((10.f*std::log10(std::max(1e-12f,peak/(512.f*512.f)))+70.f)/70.f,0.f,1.f);
            maxLevel=std::max(maxLevel,v[i]);
        }
        std::array<uint8_t,bands*2> out{};
        for(int i=0;i<bands;i++){out[2*i]=std::lround(v[i]*255);
            out[2*i+1]=std::lround(255*std::min(1.f,v[i]*2.55f)*std::max(0.f,1.f-(maxLevel-v[i])*2.55f));}
        glTexSubImage2D(GL_TEXTURE_2D,0,0,y,bands,1,GL_RG,GL_UNSIGNED_BYTE,out.data());
    }
public:
    void init(ANativeWindow* w,AAssetManager* a) {
        dpy=eglGetDisplay(EGL_DEFAULT_DISPLAY);
        if(!eglInitialize(dpy,nullptr,nullptr))throw std::runtime_error("EGL initialize");
        const EGLint configArgs[]={EGL_RENDERABLE_TYPE,EGL_OPENGL_ES3_BIT,EGL_SURFACE_TYPE,EGL_WINDOW_BIT,
            EGL_RED_SIZE,8,EGL_GREEN_SIZE,8,EGL_BLUE_SIZE,8,EGL_NONE};
        EGLConfig cfg{};EGLint count{};
        if(!eglChooseConfig(dpy,configArgs,&cfg,1,&count)||!count)throw std::runtime_error("No EGL ES3 window config");
        const EGLint ctxArgs[]={EGL_CONTEXT_CLIENT_VERSION,3,EGL_NONE};
        context=eglCreateContext(dpy,cfg,EGL_NO_CONTEXT,ctxArgs);
        surface=eglCreateWindowSurface(dpy,cfg,w,nullptr);
        if(context==EGL_NO_CONTEXT||surface==EGL_NO_SURFACE||!eglMakeCurrent(dpy,surface,surface,context))
            throw std::runtime_error("EGL window or context unavailable");
        eglSwapInterval(dpy,1);
        GLint minor=0;glGetIntegerv(GL_MINOR_VERSION,&minor);
        legacy=minor<1;
        if(!legacy)try {
            graphics=program(a,{{GL_VERTEX_SHADER,"terrain.vert"},{GL_FRAGMENT_SHADER,"terrain.frag"}});
            compute=program(a,{{GL_COMPUTE_SHADER,"spectrum.comp"}});
        } catch(const std::exception& e){LOGE("Using ES3.0 vertex textures: %s",e.what());glDeleteProgram(graphics);legacy=true;}
        glGenBuffers(3,buf);glGenVertexArrays(1,&vao);glBindVertexArray(vao);
        glBindBuffer(GL_UNIFORM_BUFFER,buf[2]);glBufferData(GL_UNIFORM_BUFFER,sizeof(Params),nullptr,GL_STREAM_DRAW);
        glBindBufferBase(GL_UNIFORM_BUFFER,0,buf[2]);
        if(legacy){
            graphics=program(a,{{GL_VERTEX_SHADER,"terrain.vert"},{GL_FRAGMENT_SHADER,"terrain.frag"}});
            for(int i=0;i<block;i++){window[i]=.5f-.5f*std::cos(6.2831853f*i/(block-1));twiddles[i]=std::polar(1.f,-6.2831853f*i/block);}
            glGenTextures(1,&texture);glBindTexture(GL_TEXTURE_2D,texture);
            glTexStorage2D(GL_TEXTURE_2D,1,GL_RG8,bands,rows);
            glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_NEAREST);glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_NEAREST);
        }else{
            for(int i=0;i<2;i++){glBindBuffer(GL_SHADER_STORAGE_BUFFER,buf[i]);glBufferData(GL_SHADER_STORAGE_BUFFER,
                i?bands*rows*2*sizeof(float):sizeof(Samples),nullptr,GL_DYNAMIC_DRAW);glBindBufferBase(GL_SHADER_STORAGE_BUFFER,i,buf[i]);}
        }
        glEnable(GL_BLEND);glBlendFunc(GL_SRC_ALPHA,GL_ONE_MINUS_SRC_ALPHA);
        LOGI("OpenGL ES %s on %s, %s",glGetString(GL_VERSION),glGetString(GL_RENDERER),legacy?"vertex textures":"GPU FFT");
    }
    int draw(const Params& params,int hops) override {
        EGLint w=0,h=0;eglQuerySurface(dpy,surface,EGL_WIDTH,&w);eglQuerySurface(dpy,surface,EGL_HEIGHT,&h);
        if(!w||!h)return 1;
        Params p=params;p.screen[0]=float(w);p.screen[1]=float(h);
        glBindBuffer(GL_UNIFORM_BUFFER,buf[2]);glBufferSubData(GL_UNIFORM_BUFFER,0,sizeof(p),&p);
        if(hops && legacy){
            for(int i=0;i<hops;++i)legacyRow(samples.data()+i*hop,(row+rows-hops+1+i)%rows);
        } else if(hops){
            glUseProgram(compute);glBindBuffer(GL_SHADER_STORAGE_BUFFER,buf[0]);
            glBufferSubData(GL_SHADER_STORAGE_BUFFER,0,(hops+1)*hop*sizeof(uint32_t),samples.data());
            glDispatchCompute(hops,1,1);glMemoryBarrier(GL_SHADER_STORAGE_BARRIER_BIT);
        }
        glViewport(0,0,w,h);glUseProgram(graphics);glDrawArraysInstanced(GL_TRIANGLE_STRIP,0,4,p.instances());
        return eglSwapBuffers(dpy,surface)?0:-1;
    }
    ~GLRenderer() override {
        if(dpy!=EGL_NO_DISPLAY){
            eglMakeCurrent(dpy,EGL_NO_SURFACE,EGL_NO_SURFACE,EGL_NO_CONTEXT);
            if(surface!=EGL_NO_SURFACE)eglDestroySurface(dpy,surface);
            if(context!=EGL_NO_CONTEXT)eglDestroyContext(dpy,context);
            eglTerminate(dpy);eglReleaseThread();
        }
    }
};
}
std::unique_ptr<Renderer> makeGL(ANativeWindow* w,AAssetManager* a){
    auto r=std::make_unique<GLRenderer>();r->init(w,a);return r;
}
