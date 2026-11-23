#version 450
#include "terrain.inc"
#ifdef VULKAN
#define INSTANCE gl_InstanceIndex
#define VERTEX gl_VertexIndex
#else
#define INSTANCE gl_InstanceID
#define VERTEX gl_VertexID
#endif
layout(location=0) out vec2 uv;
layout(location=1) out vec4 light;
layout(location=2) flat out int kind;
layout(location=3) out vec3 groundTint;
vec4 rimLight(View v,float x) {
    float dx=clamp(x,0.,1.)*u.screen.x-v.van.x;
    vec2 edge=fieldCoord(v,v.origin+dx/(v.cell-dx*v.turn),0.);
    vec2 live=spectrum(max(0.,edge.x),edge.y);
    return vec4(mix(vec3(.30,.50,.89),hue(edge.y/55.),live.y),live.x);
}
void main() {
    uv=vec2(VERTEX&1,VERTEX>>1);
    light=vec4(0.);
    groundTint=vec3(0.);
    kind=0;
    vec2 pos=uv;
    View v=camera();
    const int skyCols=64, skyRows=16, skyCount=skyCols*skyRows;
    if(INSTANCE<skyCount) {
        uv=(vec2(INSTANCE%skyCols,INSTANCE/skyCols)+uv)/vec2(skyCols,skyRows);
        pos=uv;
        vec2 delta=uv*u.screen.xy-v.van;
        // Light from adjacent parts of the rim overlaps more as it rises.
        float rim=v.van.y+v.rise*(1.-v.turn*delta.x/v.cell);
        float spread=mix(.027,.11,clamp(1.-uv.y*u.screen.y/max(1.,rim),0.,1.));
        light=(rimLight(v,uv.x-2.*spread)+4.*rimLight(v,uv.x-spread)+6.*rimLight(v,uv.x)
              +4.*rimLight(v,uv.x+spread)+rimLight(v,uv.x+2.*spread))/16.;
        float dy=max(delta.y,1.);
        float c=v.origin+delta.x*v.rise/(v.cell*dy);
        float r=(1.+v.turn*(c-v.origin)-v.rise/dy)/v.stepZ;
        vec2 loc=fieldCoord(v,c,r);
        float heat=spectrum(clamp(loc.x,0.,54.),loc.y).y;
        groundTint=hue(loc.y/55.)*(.12*heat*heat);
    } else {
        int cols=int(u.geometry.x), id=INSTANCE-skyCount;
        bool sideEdge=u.motion.w>.5;
        int markerCount=sideEdge?64:cols;
        int r;
        float c;
        // Alpha blending follows instance order: edge markers, reflections, then orbs.
        if(id<markerCount) {
            kind=3;
            r=sideEdge?id:0;
            c=sideEdge?23.:firstColumn(v,0.)+float(id);
        } else {
            int point=id-markerCount, line=point/cols;
            kind=line<64?1:2;
            r=line%64;
            c=firstColumn(v,float(r))+float(point%cols);
        }
        float d=1.+v.turn*(c-v.origin)-v.stepZ*float(r);
        vec2 coord=fieldCoord(v,c,float(r));
        if(d<.10 || coord.x<0. || coord.x>55.){gl_Position=vec4(2.,2.,0.,1.);return;}
        float spacing=v.cell/d;
        vec2 foot=v.van+vec2((c-v.origin)*spacing,v.rise/d);
        if(foot.x < -spacing || foot.x>u.screen.x+spacing || foot.y>u.screen.y+.15*min(u.screen.x,u.screen.y)+spacing){
            gl_Position=vec4(2.,2.,0.,1.);return;
        }
        vec2 s=spectrum(coord.x,coord.y);
        float lift=.065*spacing+.142*min(u.screen.x,u.screen.y)*spacing/(spacing+.7*v.cell)*s.x;
        light=vec4(mix(vec3(.37,.45,.84),hue(coord.y/55.),smoothstep(0.,.85,s.y)),s.y);
        if(kind==3){
            float width=max(1.,spacing*.061);
            foot+=vec2((uv.x-.5)*width,-uv.y*lift);
        } else {
            foot.y+=(kind==1?lift:-lift);
            float size=2.*max(kind==1?1.6:3.0,spacing*(kind==1?.20:.46));
            foot+=(uv-.5)*size;
        }
        pos=foot/u.screen.xy;
    }
    gl_Position=vec4(2.*pos-1.,0.,1.);
#ifndef VULKAN
    gl_Position.y=-gl_Position.y;
#endif
}
