#ifndef VULKAN
precision highp float;
precision highp int;
precision highp sampler2D;
#endif
#ifdef VULKAN
layout(push_constant) uniform Params { vec4 view; vec4 motion; vec4 screen; vec4 history; vec4 geometry; } u;
#else
layout(std140) uniform Params { vec4 view; vec4 motion; vec4 screen; vec4 history; vec4 geometry; } u;
#endif
#ifdef GL_LEGACY
uniform sampler2D spectrumMap;
vec2 binAt(int row,int col) {return texelFetch(spectrumMap,ivec2(col,row),0).rg;}
#else
layout(std430,binding=1) readonly buffer Spectrum {vec2 levels[];};
vec2 binAt(int row,int col) {return levels[row*56+col];}
#endif
vec2 rowAt(int age,float band) {
    if(age<0 || age>=int(u.history.y)) return vec2(0.);
    int row=(int(u.history.x)+64-age)%64;
    return mix(binAt(row,int(band)),binAt(row,min(55,int(band)+1)),fract(band));
}
vec2 spectrum(float age,float band) {
    float a=age+u.history.z;
    return mix(rowAt(int(floor(a)),band),rowAt(int(floor(a))+1,band),fract(a))*u.history.w;
}
vec3 hue(float t) {
    const vec3 colors[9]=vec3[9](vec3(.21,.29,1.),vec3(.14,.55,1.),vec3(.15,.87,.81),
        vec3(.23,.94,.45),vec3(1.,.85,.31),vec3(1.,.56,.16),vec3(1.,.32,.19),vec3(1.,.19,.54),vec3(1.,.15,.23));
    const float stops[9]=float[9](0.,.15,.22,.31,.415,.52,.64,.77,1.);
    for(int i=1;i<9;i++) if(t<=stops[i]) return mix(colors[i-1],colors[i],smoothstep(stops[i-1],stops[i],t));
    return colors[8];
}
// Same ground homography as the desktop camera. All coordinates here use a top-left origin.
struct View {vec2 van;float cell;float rise;float turn;float stepZ;float origin;float baseCell;};
View camera() {
    vec2 size=u.screen.xy, van=size*u.view.xy;
    float baseCell=size.x/u.screen.w;
    float cell=baseCell*u.motion.x/.0293;
    float rise=size.y*(mix(u.view.z,u.view.w,u.view.x)-u.view.y);
    float turn=-(u.view.w-u.view.z)*size.y/size.x*cell/rise;
    float delta=size.x*u.motion.y-van.x, depth=1./(1.-u.motion.z);
    return View(van,cell*depth,rise*depth,turn*depth,(u.motion.w>.5?.76*baseCell/size.x:.020)*depth,
        -delta/(cell-delta*turn),baseCell);
}
float firstColumn(View v,float r) {
    float dx=-v.van.x;
    return floor(v.origin+dx*(1.-v.stepZ*r)/(v.cell-dx*v.turn))-1.;
}
vec2 fieldCoord(View v,float c,float r) {
    bool side=u.motion.w>.5;
    float age=side?23.-c:r;
    float col=side?round(-u.screen.x/v.baseCell*.48)+r:c;
    float span=v.baseCell/u.screen.x, turn=.004*u.screen.y/u.screen.x*v.baseCell/(u.screen.y*.153);
    float den=1.+turn*col;
    float band=.497+col*span*mix(1./den,1./max(.05,den-.02*age),.48*smoothstep(0.,36.,age));
    return vec2(age*.88,clamp(band,0.,1.)*55.);
}
