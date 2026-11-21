(() => {
  'use strict';
  const canvas = document.getElementById('terrain');
  const ctx = canvas.getContext('2d', {alpha:false});
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const field = new SpectralField();
  const sample = {energy:0, tone:0};
  const clamp = (x, low=0, high=1) => Math.max(low, Math.min(high,x));
  const mix = (a,b,t) => a+(b-a)*t;
  const smooth = t => t*t*(3-2*t);

  // The vanishing point and the ends of the far edge are measured in fractions
  // of the view. Both grid axes are straight lines under this ground homography.
  const farLeft=.420, farRight=.355, vanishX=.184, vanishY=.19;
  const farCell=.0368, rowStep=.016;
  let w=0, h=0, view=null, backdrop=null, restingDots=null, wall=null, vertices=[], rim=[], frame=0, last=0;
  const hues=56, strengths=20, tile=46;
  // Bass stays blue, mids green and treble yellow on the logarithmic band axis.
  const stops=[
    [0,[43,94,255]], [.25,[43,132,255]], [.36,[39,203,225]],
    [.46,[39,222,125]], [.59,[79,239,86]], [.68,[136,241,76]],
    [.80,[247,235,65]], [1,[255,226,72]]
  ];
  const ice=[26,179,255];
  function color(t) {
    const i=stops.findIndex(([end])=>end>=t);
    if (i<=0) return stops[0][1];
    const [a,from]=stops[i-1], [b,to]=stops[i];
    return from.map((ch,k)=>mix(ch,to[k],smooth((t-a)/(b-a))));
  }
  // Zero stays on the floor. The logarithmic shoulder keeps a positive slope
  // all the way to full scale, and gives small changes room to show up.
  const logFour=Math.log(4);
  function reliefFraction(energy) {
    return Math.log1p(3*energy)/logFour;
  }
  function surface(width,height) {
    const result=document.createElement('canvas');
    result.width=width; result.height=height;
    return result;
  }
  const dots=surface(hues*tile,strengths*tile);
  const b=dots.getContext('2d');
  for (let v=0;v<strengths;v++) for (let u=0;u<hues;u++) {
    const tint=v/(strengths-1);
    const rgb=color(u/(hues-1)).map((c,k)=>Math.round(mix(ice[k],c,tint)));
    const core=rgb.map(c=>Math.round(mix(c,255,mix(1,.24,tint))));
    const ring=rgb.map(c=>Math.round(mix(c,255,mix(.58,.08,tint))));
    const x=(u+.5)*tile, y=(v+.5)*tile;
    const glow=b.createRadialGradient(x,y,0,x,y,tile*.5);
    glow.addColorStop(0,'rgb('+core+')'); glow.addColorStop(.199,'rgb('+core+')');
    glow.addColorStop(.235,'rgba('+ring+',1)');
    glow.addColorStop(.256,'rgba('+rgb+',.84)');
    glow.addColorStop(.37,'rgba('+rgb+','+mix(.39,.68,tint)+')');
    glow.addColorStop(.54,'rgba('+rgb+','+mix(.16,.36,tint)+')');
    glow.addColorStop(.76,'rgba('+rgb+','+mix(.06,.12,tint)+')');
    glow.addColorStop(1,'rgba('+rgb+',0)');
    b.fillStyle=glow; b.fillRect(u*tile,v*tile,tile,tile);
  }

  function project(c,r) {
    const inv=1/(1+view.turn*c-rowStep*r);
    return {x:view.vx+c*view.cell*inv, y:view.vy+view.rise*inv, spacing:view.cell*inv};
  }
  function columnAt(x,r) {
    const delta=x-view.vx;
    return delta*(1-rowStep*r)/(view.cell-delta*view.turn);
  }
  const edgeY=x=>h*farLeft+view.slope*x;

  // Smooth spatial noise shared by the moving light filaments.
  function hash(i) {
    let value=Math.imul(i+41,0x45d9f3b);
    value=Math.imul(value^(value>>>16),0x45d9f3b);
    return ((value^(value>>>16))>>>0)/4294967295;
  }
  function noise(x,period,seed) {
    const p=x/period, i=Math.floor(p);
    return mix(hash(i+seed),hash(i+1+seed),smooth(p-i));
  }

  function buildWall() {
    // Cache only the vertical falloff curves. The texture itself is redrawn each frame.
    const width=Math.min(1600,Math.ceil(canvas.width*.8)), top=112, rows=top+7;
    const texture=surface(width,rows), ink=texture.getContext('2d',{alpha:false});
    const bitmap=ink.createImageData(width,rows);
    bitmap.data.fill(255);
    const core=new Float32Array(rows), altitude=new Float32Array(rows);
    const profiles=Array.from({length:80},()=>new Float32Array(rows));
    for (let y=0;y<rows;y++) {
      const z=(top-y)/top, d=Math.abs(z), taper=1-smooth(clamp((z-.63)/.37));
      altitude[y]=z;
      core[y]=Math.exp(-d/(z>=0?.014:.008));
      profiles.forEach((curve,i)=>{
        const decay=.047+.39*i/(profiles.length-1);
        curve[y]=taper*Math.exp(-d/(z>=0?decay:.16*decay));
      });
    }
    return {texture,ink,bitmap,core,altitude,profiles,top,rows,energies:new Float32Array(73)};
  }
  function drawWall(g, now) {
    const time=reduced.matches?0:now*.001;
    const {texture,ink,bitmap,core,altitude,profiles,top,rows,energies}=wall;
    for (let i=0;i<energies.length;i++) {
      const band=(field.bands-1)*(i/(energies.length-1))**.73;
      field.sample(0,band,sample);
      const attack=sample.energy;
      field.sample(4,band,sample);
      energies[i]=1-Math.exp(-2.7*(.68*attack+.32*sample.energy));
    }
    const out=bitmap.data, width=texture.width, stride=width*4;
    for (let px=0;px<width;px++) {
      const u=(px+.5)/width, x=u*w*1152/h;
      const at=u*(energies.length-1), i=Math.floor(at);
      const energy=mix(energies[i],energies[i+1],at-i);
      const cloud=.65*noise(x-time*78,163,27)+.35*noise(x+time*33,369,53);
      const wind=x-time*12+15*Math.sin(.006*x+time*.85);
      const filament=.66*noise(wind,4.5,97)**3+.34*noise(x+time*10,13,171)**2;
      const rise=(time*.38+1.6*noise(x,133,418))%1;
      const head=.72*rise, pulse=Math.sin(Math.PI*rise)**2;
      const fog=profiles[Math.round(7+17*cloud+19*energy)];
      const shaft=profiles[Math.round(5+34*cloud+15*filament+24*energy)];
      // Reduced motion keeps the filaments in place and varies only their luminance.
      const breathing=reduced.matches ? .94+.09*Math.sin(now*.00062+u*9) : 1;
      const seam=(.43+.48*cloud+.32*energy)*breathing;
      const mist=(.23+.55*cloud+.58*energy)*breathing;
      const rays=filament*(.44+.94*cloud+.80*energy)*breathing;
      for (let y=0,k=px*4;y<rows;y++,k+=stride) {
        const z=altitude[y], offset=(z-head)*6.5;
        const crest=Math.max(0,1-offset*offset);
        const light=rays*shaft[y]*(.66+2.2*pulse*crest*crest);
        const base=seam*core[y], air=mist*fog[y];
        out[k]=219*base+62*air+112*light;
        out[k+1]=249*base+153*air+191*light;
        out[k+2]=255*base+222*air+247*light;
      }
    }
    ink.putImageData(bitmap,0,0);
    const rowHeight=h*.34/top;
    g.save(); g.transform(1,view.slope,0,1,0,h*farLeft);
    g.globalCompositeOperation='lighter';
    g.drawImage(texture,0,-top*rowHeight,w,rows*rowHeight);
    g.restore();
  }

  function buildSurface() {
    vertices=[]; rim=[];
    // Include points whose foot is below the window but whose peak can enter it.
    const margin=h*.075;
    for (let r=0;r<1/rowStep-2;r++) {
      const c0=Math.floor(columnAt(-margin,r))-1, c1=Math.ceil(columnAt(w+margin,r))+1;
      for (let c=c0;c<=c1;c++) {
        const p=project(c,r);
        if (p.spacing<=0 || p.x< -margin || p.x>w+margin) continue;
        p.foot=p.y-p.spacing*.13;
        // Nearby cells appear much larger. Limit their projected excursion
        // smoothly as well, so the front of the map stays anchored.
        p.reliefPx=.05*h*p.spacing/(p.spacing+view.cell);
        if (p.foot-p.reliefPx>h+margin) continue;
        const atBack=project(c,0);
        // Spread the frequency slices over the visible front as well as the
        // back edge, so treble does not run out of view with perspective.
        const u=clamp(mix(atBack.x,p.x,.88*smooth(clamp(r/40)))/w);
        p.band=(field.bands-1)*u**.73;
        p.age=r*.78;
        vertices.push(p);
        if (r===0) rim.push(p);
      }
    }
  }
  function paintFloor(g) {
    const sky=g.createLinearGradient(w,0,0,h*.47);
    sky.addColorStop(0,'#020d20'); sky.addColorStop(.58,'#031022'); sky.addColorStop(1,'#071f36');
    g.fillStyle=sky; g.fillRect(0,0,w,h);
    g.save();
    g.beginPath(); g.moveTo(0,edgeY(0)); g.lineTo(w,edgeY(w));
    g.lineTo(w,h); g.lineTo(0,h); g.closePath(); g.clip();
    const ground=g.createLinearGradient(0,h*.355,0,h);
    ground.addColorStop(0,'#05192d'); ground.addColorStop(.35,'#04172b'); ground.addColorStop(1,'#031126');
    g.fillStyle=ground; g.fillRect(0,0,w,h);

    // An offset, fixed grid is printed on the floor under the light field.
    // Its lines do not share the coordinates or the displacement of the dots.
    const ends=[columnAt(-h*.05,0),columnAt(w+h*.05,0)];
    function segment(a,b) {g.moveTo(a.x,a.y);g.lineTo(b.x,b.y);}
    for (const major of [false,true]) {
      const ink=g.createLinearGradient(0,h*.37,0,h);
      ink.addColorStop(0,major?'#a8daff24':'#6094ba08');
      ink.addColorStop(.29,major?'#a8daff60':'#759abb24');
      ink.addColorStop(.76,major?'#d7edffa6':'#98b6c788');
      ink.addColorStop(1,major?'#e6f7fff2':'#c0d4ddc2');
      g.strokeStyle=ink; g.lineWidth=Math.max(.5,h/1152*(major?1.45:.97));
      g.beginPath();
      for (let c=Math.floor(ends[0])-1;c<=Math.ceil(ends[1])+1;c++) {
        if ((c%9===0)!==major) continue;
        // Project to a near clipping plane before the perspective singularity.
        const end=(1+view.turn*(c+.43)-.12)/rowStep;
        segment(project(c+.43,0),project(c+.43,end));
      }
      for (let r=0;r<1/rowStep-2;r++) {
        if ((r%12===0)!==major) continue;
        const z=r+.45;
        segment(project(columnAt(0,z),z),project(columnAt(w,z),z));
      }
      g.stroke();
    }
    g.fillStyle='#2b9fec';
    for (const p of vertices) {
      const radius=p.spacing*.025;
      if (p.y>h+radius) continue;
      g.globalAlpha=.48+.32*clamp((p.y/h-.4)*1.6);
      g.beginPath();g.arc(p.x+p.spacing*.18,p.y+radius,radius,0,Math.PI*2);g.fill();
    }
    g.restore();
  }

  const hazePatches=[
    [.02,.05,.38,.22,'16,97,162',.085],
    [.15,.21,.18,.13,'64,90,151',.19], [.735,.145,.089,.051,'104,120,158',.19],
    [.77,.128,.058,.023,'112,122,154',.15], [.05,.34,.09,.105,'51,89,162',.21],
    [.075,.804,.096,.036,'53,96,200',.31], [.849,.879,.087,.028,'67,88,221',.30]
  ].map(([x,y,rx,ry,rgb,alpha])=>{
    const sprite=surface(128,128), g=sprite.getContext('2d');
    const light=g.createRadialGradient(64,64,0,64,64,64);
    light.addColorStop(0,'rgb('+rgb+')'); light.addColorStop(1,'rgba('+rgb+',0)');
    g.fillStyle=light;g.fillRect(0,0,128,128);
    return {x,y,rx,ry,alpha,sprite,band:(field.bands-1)*x**.73};
  });
  function drawHaze(now) {
    ctx.save();ctx.globalCompositeOperation='screen';
    const time=reduced.matches?0:now*.00057;
    for (const p of hazePatches) {
      field.sample(2,p.band,sample);
      const phase=13*p.x+3*p.y;
      const drift=Math.sin(time+phase), roll=Math.cos(time*.76+phase);
      const lift=1-Math.exp(-2.1*sample.energy);
      const width=w*p.rx*(1+.18*roll+.23*lift), height=h*p.ry*(1+.22*drift+.30*lift);
      const cx=w*(p.x+.46*p.rx*drift), cy=h*(p.y+.29*p.ry*roll);
      ctx.globalAlpha=p.alpha*(.9+.31*roll+1.04*lift);
      ctx.drawImage(p.sprite,cx-width,cy-height,width*2,height*2);
    }
    ctx.restore();
  }

  // Narrow columns of emitted light occur only at the far rim. The ground
  // surface itself is drawn exclusively with individual point sprites.
  const beamW=72, beamH=144, mask=surface(beamW,beamH);
  const maskContext=mask.getContext('2d');
  const beamImage=maskContext.createImageData(beamW,beamH);
  for (let y=0;y<beamH;y++) for (let x=0;x<beamW;x++) {
    const offset=(2*x+1)/beamW-1, altitude=(beamH-1-y)/(beamH-1);
    const k=(y*beamW+x)*4;
    const haze=Math.exp(-offset*offset*4), spine=Math.exp(-offset*offset*142);
    beamImage.data[k+3]=255*(.4*haze+.6*spine)*(1-altitude)**1.8;
  }
  maskContext.putImageData(beamImage,0,0);
  const flare=surface(beamW*hues,beamH), beam=flare.getContext('2d');
  for (let u=0;u<hues;u++) beam.drawImage(mask,u*beamW,0);
  beam.globalCompositeOperation='source-atop';
  for (let u=0;u<hues;u++) {
    beam.fillStyle='rgb('+color(u/(hues-1)).map(Math.round)+')';
    beam.fillRect(u*beamW,0,beamW,beamH);
  }
  function resize() {
    w=canvas.clientWidth; h=canvas.clientHeight;
    if (!w || !h) return;
    const dpr=Math.min(devicePixelRatio||1,1.5);
    canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);
    ctx.setTransform(dpr,0,0,dpr,0,0);
    const slope=(farRight-farLeft)*h/w;
    const rise=h*(farLeft+(farRight-farLeft)*vanishX-vanishY);
    const cell=h*farCell;
    view={vx:w*vanishX,vy:h*vanishY,rise,cell,slope,turn:-slope*cell/rise};
    buildSurface();
    backdrop=surface(canvas.width,canvas.height);
    const g=backdrop.getContext('2d');g.setTransform(dpr,0,0,dpr,0,0);
    paintFloor(g);
    wall=buildWall();
    restingDots=surface(canvas.width,canvas.height);
    const dotsCtx=restingDots.getContext('2d');dotsCtx.setTransform(dpr,0,0,dpr,0,0);
    for (const p of vertices) {
      const size=p.spacing*.433;
      dotsCtx.drawImage(dots,0,0,tile,tile,p.x-size/2,p.foot-size/2,size,size);
    }
    draw();
    wake();
  }
  function draw(now=performance.now()) {
    if (!backdrop || document.hidden) return;
    ctx.globalAlpha=1;
    ctx.drawImage(backdrop,0,0,w,h);
    drawHaze(now);
    drawWall(ctx,now);
    if (!field.gain) {ctx.drawImage(restingDots,0,0,w,h);return;}
    for (const p of rim) {
      field.sample(0,p.band,sample);
      const e=sample.energy;
      if (e<.025) continue;
      const height=.68*p.spacing+1.35*p.reliefPx*reliefFraction(e);
      const width=p.spacing*(1.25+.55*e);
      const hue=Math.round(sample.tone*(hues-1));
      ctx.globalAlpha=1-Math.exp(-5*e);
      ctx.drawImage(flare,hue*beamW,0,beamW,beamH,p.x-width/2,edgeY(p.x)-height,width,height);
    }
    ctx.globalAlpha=1;
    const relief=reduced.matches?.48:1;
    for (const p of vertices) {
      field.sample(p.age,p.band,sample);
      const e=sample.energy;
      const y=p.foot-p.reliefPx*reliefFraction(e)*relief;
      const size=p.spacing*(.433+.036*e);
      if (y-size/2>h || p.x+size/2<0 || p.x-size/2>w) continue;
      const tone=clamp(Math.round(sample.tone*(hues-1)),0,hues-1);
      const v=Math.round((1-Math.exp(-9*e))*(strengths-1));
      ctx.drawImage(dots,tone*tile,v*tile,tile,tile,p.x-size/2,y-size/2,size,size);
    }
  }
  function animate(now) {
    frame=0;
    if (document.hidden) return;
    const elapsed=now-last, live=field.playing || field.gain>0;
    const interval=reduced.matches?(live?90:250):(live?1000/30:1000/24);
    if (elapsed<interval-.5) {frame=requestAnimationFrame(animate);return;}
    last=now;
    field.advance(now,Math.min(250,elapsed));
    draw(now);
    frame=requestAnimationFrame(animate);
  }
  function wake() {
    if (!frame && !document.hidden) {
      last=performance.now()-33;frame=requestAnimationFrame(animate);
    }
  }
  window.updateSandTable=state=>{
    field.ingest(state,performance.now());
    if ((field.playing && field.newest>=0) || field.gain>0) wake();
  };
  document.addEventListener('visibilitychange',()=>{
    if (document.hidden) {cancelAnimationFrame(frame);frame=0;}
    else {draw();wake();}
  });
  reduced.addEventListener('change',()=>{draw();wake();});
  new ResizeObserver(resize).observe(canvas);
})();
