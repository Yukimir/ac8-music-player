#version 450
#include "terrain.inc"
layout(location=0) in vec2 uv;
layout(location=1) in vec4 light;
layout(location=2) flat in int kind;
layout(location=3) in vec3 groundTint;
layout(location=0) out vec4 color;
void main() {
    if(kind==0) {
        View v=camera();
        vec2 pixel=uv*u.screen.xy;
        float dx=pixel.x-v.van.x, dy=pixel.y-v.van.y;
        float rim=v.van.y+v.rise*(1.-v.turn*dx/v.cell);
        float z=(pixel.y-rim)/u.screen.y;
        float c=v.origin+dx*v.rise/(v.cell*max(dy,1.));
        float r=(1.+v.turn*(c-v.origin)-v.rise/max(dy,1.))/v.stepZ;
        // Scale the curtain to the actual rim at each x, including tilted cruise shots.
        float altitude=clamp((rim-pixel.y)/max(rim,1.),0.,1.);
        float fade=.14+.86*(1.-altitude)*(1.-.55*altitude);
        float sheet=(.32+.17*light.a)*(z<0.?fade:exp(-75.*z));
        float beam=.58*exp(-380.*abs(z))+.16*exp(-48.*abs(z));
        vec3 sky=mix(vec3(.009,.022,.05),vec3(.026,.064,.11),smoothstep(0.,.66,uv.y));
        vec2 grid=vec2(c,r)-.43;
        vec2 dist=abs(fract(grid+.5)-.5)/max(fwidth(grid),vec2(.001));
        float mark=(1.-smoothstep(.15,.95,min(dist.x,dist.y)))*smoothstep(0.,.24,z);
        if(z>=0.)sky=vec3(.018,.031,.062)+mark*vec3(.14,.19,.28)+groundTint;
        color=vec4(sky+light.rgb*sheet+mix(light.rgb,vec3(.83,.94,1.),.45)*beam,1.);
    } else if(kind==3) {
        color=vec4(light.rgb,.20+.52*light.a);
    } else {
        float dist=length(uv-.5)*2.;
        if(dist>1.)discard;
        float core=1.-smoothstep(.25,.49,dist);
        float halo=exp(-4.6*dist)*(1.-smoothstep(.7,1.,dist));
        float alpha=kind==1?.48*(core+.4*halo):min(1.,core+1.65*halo);
        color=vec4(kind==1?light.rgb:mix(light.rgb,vec3(.92,.98,1.),core*.95),alpha);
    }
}
