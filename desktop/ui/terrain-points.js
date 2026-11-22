'use strict';

// A transparent foreground over the terrain. The same atlas, projected ground
// coordinates and spectral rows also drive the Canvas 2D renderer.
class TerrainPoints {
  static create(base,dots,mirror,tile,field) {
    const layer=document.createElement('canvas');
    layer.id='terrain-points';
    layer.setAttribute('aria-hidden','true');
    let gl;
    try {
      gl=layer.getContext('webgl2',{alpha:true,premultipliedAlpha:true,depth:false,
        stencil:false,antialias:false,failIfMajorPerformanceCaveat:true});
    } catch {return null;}
    if (!gl) return null;
    const points=new TerrainPoints(gl,dots,mirror,tile,field);
    const initialize=()=>{
      try {
        points.initialize();
        points.ready=true;
      } catch (error) {
        console.warn('GPU terrain points unavailable',error);
        gl.getExtension('WEBGL_lose_context')?.loseContext();
        points.ready=false;
      }
      layer.hidden=!points.ready;
    };
    initialize();
    if (!points.ready) return null;
    layer.addEventListener('webglcontextlost',event=>{
      event.preventDefault();points.ready=false;layer.hidden=true;
    });
    layer.addEventListener('webglcontextrestored',initialize);
    base.after(layer);
    return points;
  }

  constructor(gl,dots,mirror,tile,field) {
    this.gl=gl;
    this.images=[mirror,dots];
    this.tile=tile;
    this.field=field;
    this.vertices=new Float32Array(0);
    this.history=new Float32Array(field.capacity*field.bands*4);
    this.rowIds=new Float64Array(field.capacity);
  }

  initialize() {
    const gl=this.gl, field=this.field;
    const program=gl.createProgram();
    for (const [type,source] of [[gl.VERTEX_SHADER,TerrainPoints.vertex],[gl.FRAGMENT_SHADER,TerrainPoints.fragment]]) {
      const shader=gl.createShader(type);
      gl.shaderSource(shader,source);gl.compileShader(shader);
      if (!gl.getShaderParameter(shader,gl.COMPILE_STATUS)) throw Error(gl.getShaderInfoLog(shader));
      gl.attachShader(program,shader);gl.deleteShader(shader);
    }
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program,gl.LINK_STATUS)) throw Error(gl.getProgramInfoLog(program));
    gl.useProgram(program);
    const uniform=name=>gl.getUniformLocation(program,name);
    this.screen=uniform('screen');this.clock=uniform('clock');this.reflection=uniform('reflection');
    gl.uniform2f(uniform('tiles'),this.images[0].width/this.tile,this.images[0].height/this.tile);
    gl.uniform1i(uniform('atlas'),0);gl.uniform1i(uniform('history'),1);
    this.buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,this.buffer);
    for (const [index,size,offset] of [[0,4,0],[1,2,16]]) {
      gl.enableVertexAttribArray(index);gl.vertexAttribPointer(index,size,gl.FLOAT,false,24,offset);
      gl.vertexAttribDivisor(index,1);
    }
    const texture=filter=>{
      const t=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,t);
      for (const p of [gl.TEXTURE_MIN_FILTER,gl.TEXTURE_MAG_FILTER]) gl.texParameteri(gl.TEXTURE_2D,p,filter);
      for (const p of [gl.TEXTURE_WRAP_S,gl.TEXTURE_WRAP_T]) gl.texParameteri(gl.TEXTURE_2D,p,gl.CLAMP_TO_EDGE);
      return t;
    };
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL,true);
    this.atlases=this.images.map(image=>{
      const t=texture(gl.LINEAR);
      gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,image);
      return t;
    });
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL,false);
    gl.activeTexture(gl.TEXTURE1);
    texture(gl.NEAREST);
    gl.texStorage2D(gl.TEXTURE_2D,1,gl.RGBA32F,field.bands,field.capacity);
    gl.enable(gl.BLEND);gl.blendFunc(gl.ONE,gl.ONE_MINUS_SRC_ALPHA);
    if (gl.getError()!==gl.NO_ERROR) throw Error('Could not allocate terrain textures');
    this.rowIds.fill(-2);
    this.view=null;
  }

  uploadHistory() {
    const f=this.field, gl=this.gl;
    if (this.epoch!==f.epoch || this.song!==f.song) this.rowIds.fill(-2);
    this.epoch=f.epoch;this.song=f.song;
    let changed=false;
    for (let y=0;y<f.capacity;y++) {
      const row=f.rows[y];
      if (this.rowIds[y]===row.number) continue;
      this.rowIds[y]=row.number;changed=true;
      let i=y*f.bands*4;
      for (let x=0;x<f.bands;x++) {
        this.history[i++]=row.energy[x];
        this.history[i++]=row.energy[x]*row.tone[x];
        this.history[i++]=row.heat[x];
        this.history[i++]=row.number%(2*f.capacity);
      }
    }
    if (changed) {
      gl.activeTexture(gl.TEXTURE1);
      gl.texSubImage2D(gl.TEXTURE_2D,0,0,0,f.bands,f.capacity,gl.RGBA,gl.FLOAT,this.history);
    }
  }

  draw(points,view,width,height,pixelsWide,pixelsHigh) {
    const gl=this.gl, layer=gl.canvas;
    if (layer.width!==pixelsWide || layer.height!==pixelsHigh) {
      layer.width=pixelsWide;layer.height=pixelsHigh;
    }
    gl.viewport(0,0,pixelsWide,pixelsHigh);
    if (this.view!==view) {
      const length=points.length*6;
      if (this.vertices.length<length) this.vertices=new Float32Array(length+1536);
      let i=0;
      for (const p of points) {
        this.vertices[i++]=p.x;this.vertices[i++]=p.y;this.vertices[i++]=p.spacing;
        this.vertices[i++]=p.reliefPx;this.vertices[i++]=p.age;this.vertices[i++]=p.band;
      }
      gl.bufferData(gl.ARRAY_BUFFER,this.vertices.subarray(0,length),gl.DYNAMIC_DRAW);
      this.view=view;
    }
    this.uploadHistory();
    gl.uniform2f(this.screen,width,height);
    gl.uniform2f(this.clock,this.field.cursor%(2*this.field.capacity),this.field.gain);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.activeTexture(gl.TEXTURE0);
    for (let pass=0;pass<2;pass++) {
      gl.bindTexture(gl.TEXTURE_2D,this.atlases[pass]);
      gl.uniform1i(this.reflection,pass===0?1:0);
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP,0,4,points.length);
    }
  }

  static vertex=`#version 300 es
    precision highp float;
    layout(location=0) in vec4 point;
    layout(location=1) in vec2 ageBand;
    uniform vec2 screen, clock, tiles;
    uniform bool reflection;
    uniform highp sampler2D history;
    out vec2 uv;

    vec3 slice(float frame,float band) {
      ivec2 size=textureSize(history,0);
      float tag=mod(frame,float(2*size.y));
      int row=int(tag)%size.y, col=int(band);
      vec4 a=texelFetch(history,ivec2(col,row),0);
      if (a.w!=tag) return vec3(0);
      vec4 b=texelFetch(history,ivec2(min(col+1,size.x-1),row),0);
      return mix(a.xyz,b.xyz,fract(band));
    }
    void main() {
      float frame=clock.x-ageBand.x;
      vec3 s=mix(slice(floor(frame),ageBand.y),slice(floor(frame)+1.,ageBand.y),fract(frame));
      float tone=s.x>0. && clock.y>0. ? s.y/s.x : 0.;
      vec2 tile=floor(clamp(vec2(tone,s.z*clock.y),0.,1.)*(tiles-1.)+.5);
      vec2 corner=vec2(gl_VertexID&1,gl_VertexID>>1);
      uv=(tile+corner)/tiles;
      float lift=point.z*.065+point.w*s.x*clock.y;
      vec2 center=point.xy+vec2(0.,reflection?lift:-lift);
      vec2 pixel=center+(corner-.5)*point.z*(reflection?.16:.36);
      gl_Position=vec4(pixel/screen*vec2(2.,-2.)+vec2(-1.,1.),0.,1.);
    }`;
  static fragment=`#version 300 es
    precision highp float;
    uniform sampler2D atlas;
    in vec2 uv;
    out vec4 color;
    void main() {color=texture(atlas,uv);}`;
}
