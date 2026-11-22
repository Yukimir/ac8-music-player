(() => {
  'use strict';
  const canvas = document.getElementById('terrain');
  const ctx = canvas.getContext('2d', {alpha:false});
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const field = new SpectralField();
  const sample = {energy:0, tone:0, heat:0};
  const clamp = (x, low=0, high=1) => Math.max(low, Math.min(high,x));
  const mix = (a,b,t) => a+(b-a)*t;
  const smooth = t => t*t*(3-2*t);

  // The vanishing point and the ends of the far edge are measured in fractions
  // of the view. Both grid axes are straight lines under this ground homography.
  const camera=new TerrainCamera(), rowStep=.020;
  let rendering=!document.body.hasAttribute('data-wallpaper');
  let w=0, h=0, reference=null, view=null, backdrop=null, restingDots=null, wall=null, floorLight=null;
  let vertices=[], rim=[], frame=0, last=0, nextDraw=0;
  const hues=56, strengths=20, tile=46;
  // Frequency runs logarithmically from 38 Hz to 16.5 kHz. The cool
  // background is visible between the colored areas of spectral activity.
  const stops=[
    [0,[53,74,255]], [.15,[35,139,255]], [.22,[37,221,206]],
    [.31,[58,239,114]], [.415,[255,218,80]], [.52,[255,142,40]],
    [.64,[255,81,49]], [.77,[255,48,137]], [1,[255,38,59]]
  ];
  const ice=[107,100,203];
  function color(t) {
    const i=stops.findIndex(([end])=>end>=t);
    if (i<=0) return stops[0][1];
    const [a,from]=stops[i-1], [b,to]=stops[i];
    return from.map((ch,k)=>mix(ch,to[k],smooth((t-a)/(b-a))));
  }
  function surface(width,height) {
    const result=document.createElement('canvas');
    result.width=width; result.height=height;
    return result;
  }
  const dots=surface(hues*tile,strengths*tile);
  const b=dots.getContext('2d');
  const mirror=surface(dots.width,dots.height), m=mirror.getContext('2d');
  const colors=Array.from({length:hues},(_,i)=>color(i/(hues-1)).map(Math.round));
  for (let v=0;v<strengths;v++) for (let u=0;u<hues;u++) {
    const tint=v/(strengths-1);
    const saturation=1-(1-tint)**2.4;
    const rgb=colors[u].map((c,k)=>Math.round(mix(ice[k],c,saturation)));
    const core=rgb.map(c=>Math.round(mix(c,255,.91)));
    const ring=rgb.map(c=>Math.round(mix(c,255,.28)));
    const x=(u+.5)*tile, y=(v+.5)*tile;
    const glow=b.createRadialGradient(x,y,0,x,y,tile*.5);
    glow.addColorStop(0,'rgb('+core+')'); glow.addColorStop(.18,'rgb('+core+')');
    glow.addColorStop(.235,'rgba('+ring+',1)');
    glow.addColorStop(.256,'rgba('+rgb+',.84)');
    glow.addColorStop(.37,'rgba('+rgb+','+mix(.39,.68,tint)+')');
    glow.addColorStop(.54,'rgba('+rgb+','+mix(.16,.36,tint)+')');
    glow.addColorStop(.76,'rgba('+rgb+','+mix(.06,.12,tint)+')');
    glow.addColorStop(1,'rgba('+rgb+',0)');
    b.fillStyle=glow; b.fillRect(u*tile,v*tile,tile,tile);
    // A small, colored image below the ground plane with no white emitter.
    const reflected=m.createRadialGradient(x,y,0,x,y,tile*.5);
    reflected.addColorStop(0,'rgba('+rgb+','+mix(.35,.64,tint)+')');
    reflected.addColorStop(.30,'rgba('+rgb+','+mix(.28,.54,tint)+')');
    reflected.addColorStop(.38,'rgba('+rgb+',.07)');
    reflected.addColorStop(1,'rgba('+rgb+',0)');
    m.fillStyle=reflected;m.fillRect(u*tile,v*tile,tile,tile);
  }

  function makeView(pose) {
    const side=Boolean(pose.side);
    const vx=w*pose.vx, vy=h*pose.vy, cell=(side?w:h)*pose.cell;
    const slope=(pose.right-pose.left)*h/w;
    const rise=h*(pose.left+(pose.right-pose.left)*pose.vx-pose.vy);
    const turn=-slope*cell/rise;
    const delta=w*pose.aimX-vx;
    const depth=1/(1-(pose.forward || 0));
    // Viewed from the side, the frequency axis recedes into the distance.
    const columns=side?w/reference.cell:0;
    const step=side?.76/columns:rowStep;
    const origin=-delta/(cell-delta*turn);
    return {vx,vy,cell:cell*depth,slope,rise:rise*depth,turn:turn*depth,step:step*depth,
      edge:vy+(h*pose.left-vy)*depth+slope*vx*(depth-1),origin,side,sideFar:Math.round(-columns*.48)};
  }
  function project(c,r,p={}) {
    const col=c-view.origin, inv=1/(1+view.turn*col-view.step*r);
    p.spacing=view.cell*inv;
    p.x=view.vx+col*p.spacing;
    p.y=view.vy+view.rise*inv;
    return p;
  }
  function columnAt(x,r) {
    const delta=x-view.vx;
    return view.origin+delta*(1-view.step*r)/(view.cell-delta*view.turn);
  }
  const edgeY=x=>view.edge+view.slope*x;

  function bandAt(c,r) {
    // Frequency belongs to the world grid, even when the camera moves.
    const denom=1+reference.turn*c;
    const back=reference.vx+c*reference.cell/denom;
    const x=reference.vx+c*reference.cell/(denom-rowStep*r);
    return (field.bands-1)*clamp(mix(back,x,.48*smooth(clamp(r/36)))/w);
  }

  function locateSample(c,r,out) {
    // A quarter turn exchanges the grid axes. History now runs across the
    // screen and the live edge is a side rail. All lighting uses this same map.
    const history=view.side?23-c:r;
    out.age=history*.88;
    if (view.side && (history<0 || history>46)) {out.age=-1;out.band=0;return;}
    out.band=bandAt(view.side?view.sideFar+r:c,history);
  }
  const location={age:0,band:0};

  function buildFloorLight() {
    const texture=surface(Math.min(96,Math.ceil(w/14)),32);
    const ink=texture.getContext('2d',{alpha:false});
    const image=ink.createImageData(texture.width,texture.height);
    image.data.fill(255);
    const length=texture.width*texture.height;
    return {texture,ink,image,ages:new Float32Array(length),bands:new Float32Array(length),
      pose:null,height:0,updated:-Infinity};
  }
  function placeFloorLight(light) {
    const {texture,ages,bands}=light;
    const height=h-Math.min(edgeY(0),edgeY(w));
    for (let y=0;y<texture.height;y++) for (let x=0;x<texture.width;x++) {
      const px=w*(x+.5)/texture.width;
      const py=edgeY(px)+height*(y+.5)/texture.height;
      const scale=(py-view.vy)/view.rise, c=(px-view.vx)/(view.cell*scale);
      const r=(1+view.turn*c-1/scale)/view.step;
      const i=y*texture.width+x;
      locateSample(view.origin+c,r,location);
      ages[i]=location.age;bands[i]=location.band;
    }
    light.height=height;light.pose=view;
  }

  function drawFloorLight(now) {
    const {texture,ink,image,ages,bands}=floorLight;
    if (now-floorLight.updated>=32) {
      if (floorLight.pose!==view) placeFloorLight(floorLight);
      for (let i=0;i<ages.length;i++) {
        field.sample(ages[i],bands[i],sample);
        const rgb=colors[Math.round(sample.tone*(hues-1))];
        const glow=ages[i]>=0?.22*sample.heat*sample.heat:0;
        for (let k=0;k<3;k++) image.data[4*i+k]=rgb[k]*glow;
      }
      ink.putImageData(image,0,0);floorLight.updated=now;
    }
    ctx.save();ctx.globalCompositeOperation='lighter';
    ctx.transform(1,view.slope,0,1,0,edgeY(0));
    ctx.drawImage(texture,0,0,w,floorLight.height);ctx.restore();
  }

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
    const width=Math.min(1200,Math.ceil(canvas.width*.72)), top=88, rows=top+5;
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
    const count=73;
    return {texture,ink,bitmap,core,altitude,profiles,top,rows,updated:-Infinity,
      energies:new Float32Array(count), tints:new Float32Array(count*3)};
  }
  function updateWall(now) {
    const time=reduced.matches?0:now*.001;
    const {texture,ink,bitmap,core,altitude,profiles,rows,energies,tints}=wall;
    for (let i=0;i<energies.length;i++) {
      locateSample(columnAt(w*i/(energies.length-1),0),0,location);
      field.sample(location.age,location.band,sample);
      if (location.age<0) sample.energy=sample.heat=0;
      energies[i]=sample.energy;
      const rgb=colors[Math.round(sample.tone*(hues-1))];
      for (let k=0;k<3;k++) tints[3*i+k]=mix(ice[k],rgb[k],sample.heat);
    }
    const out=bitmap.data, width=texture.width, stride=width*4;
    for (let px=0;px<width;px++) {
      const u=(px+.5)/width, x=u*w*1152/h;
      const at=u*(energies.length-1), i=Math.floor(at);
      const energy=mix(energies[i],energies[i+1],at-i);
      const r=mix(tints[3*i],tints[3*i+3],at-i);
      const g=mix(tints[3*i+1],tints[3*i+4],at-i);
      const b=mix(tints[3*i+2],tints[3*i+5],at-i);
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
        const fogLight=.23*air+.35*light+.65*base;
        out[k]=r*fogLight+47*base;
        out[k+1]=g*fogLight+55*base;
        out[k+2]=b*fogLight+69*base+19*light;
      }
    }
    ink.putImageData(bitmap,0,0);
    wall.updated=now;
  }
  function drawWall(g, now) {
    if (now-wall.updated>=32) updateWall(now);
    const {texture,top,rows}=wall;
    const rowHeight=h*.34/top;
    g.save(); g.transform(1,view.slope,0,1,0,view.edge);
    g.globalCompositeOperation='lighter';
    g.drawImage(texture,0,-top*rowHeight,w,rows*rowHeight);
    g.restore();
  }

  function buildSurface() {
    let count=0;
    rim.length=0;
    // Include points whose foot is below the window but whose peak can enter it.
    const margin=h*.075;
    for (let r=0;r<1/view.step-2;r++) {
      const c0=Math.floor(columnAt(-margin,r))-1, c1=Math.ceil(columnAt(w+margin,r))+1;
      for (let c=c0;c<=c1;c++) {
        const p=project(c,r,vertices[count] || {});
        if (p.spacing<=0 || p.x< -margin || p.x>w+margin) continue;
        p.hover=p.spacing*.065;
        p.foot=p.y-p.hover;
        p.reliefPx=.142*h*p.spacing/(p.spacing+.7*view.cell);
        if (p.foot-p.reliefPx>h+margin) continue;
        locateSample(c,r,p);
        if (p.age<0) continue;
        vertices[count++]=p;
        if (r===0 || (view.side && p.age===0)) rim.push(p);
      }
    }
    vertices.length=count;
  }
  function paintFloor(g) {
    const sky=g.createLinearGradient(w,0,0,h*.47);
    sky.addColorStop(0,'#020611'); sky.addColorStop(.58,'#030a15'); sky.addColorStop(1,'#061423');
    g.fillStyle=sky; g.fillRect(0,0,w,h);
    g.save();
    g.beginPath(); g.moveTo(0,edgeY(0)); g.lineTo(w,edgeY(w));
    g.lineTo(w,h); g.lineTo(0,h); g.closePath(); g.clip();
    const ground=g.createLinearGradient(0,h*.355,0,h);
    ground.addColorStop(0,'#080d1a'); ground.addColorStop(.35,'#080b17'); ground.addColorStop(1,'#050912');
    g.fillStyle=ground; g.fillRect(0,0,w,h);

    // An offset, fixed grid is printed on the floor under the light field.
    // Its lines do not share the coordinates or the displacement of the dots.
    const ends=[columnAt(-h*.05,0),columnAt(w+h*.05,0)];
    function segment(a,b) {g.moveTo(a.x,a.y);g.lineTo(b.x,b.y);}
    for (const major of [false,true]) {
      const ink=g.createLinearGradient(0,h*.37,0,h);
      ink.addColorStop(0,major?'#a8daff24':'#6094ba08');
      ink.addColorStop(.29,major?'#a8daff60':'#759abb24');
      ink.addColorStop(.76,major?'#dbdbf68c':'#9baac347');
      ink.addColorStop(1,major?'#e4e5ffd4':'#a3afd071');
      g.strokeStyle=ink; g.lineWidth=Math.max(.5,h/1152*(major?1.45:.97));
      g.beginPath();
      for (let c=Math.floor(ends[0])-1;c<=Math.ceil(ends[1])+1;c++) {
        if ((c%9===0)!==major) continue;
        // Project to a near clipping plane before the perspective singularity.
        const end=(1+view.turn*(c+.43-view.origin)-.12)/view.step;
        segment(project(c+.43,0),project(c+.43,end));
      }
      for (let r=0;r<1/view.step-2;r++) {
        if ((r%12===0)!==major) continue;
        const z=r+.45;
        segment(project(columnAt(0,z),z),project(columnAt(w,z),z));
      }
      g.stroke();
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
    return {x,y,rx,ry,alpha,sprite,band:(field.bands-1)*x};
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
    beam.fillStyle='rgb('+colors[u]+')';
    beam.fillRect(u*beamW,0,beamW,beamH);
  }

  const gpu=TerrainPoints.create(canvas,dots,mirror,tile,field);

  function preparePoints(points) {
    for (const p of points) {
      field.sample(p.age,p.band,sample);
      p.lift=p.reliefPx*sample.energy;
      p.tint=Math.round(sample.tone*(hues-1));
      p.strength=Math.round(sample.heat*(strengths-1));
    }
  }

  function drawPoints(g) {
    // Reflect about the local projected ground at this column AND depth.
    // Paint the entire reflected surface first so a near reflection cannot
    // cover an emitter further back. Both layers use the same audio slice.
    for (const reflected of [true,false]) {
      const sprite=reflected?mirror:dots;
      for (const p of vertices) {
        const size=p.spacing*(reflected?.16:.36);
        const y=reflected?p.y+p.hover+p.lift:p.foot-p.lift;
        if (y-size/2>h || p.x+size/2<0 || p.x-size/2>w) continue;
        g.drawImage(sprite,p.tint*tile,p.strength*tile,tile,tile,p.x-size/2,y-size/2,size,size);
      }
    }
  }

  function resize() {
    if (!rendering) return;
    w=canvas.clientWidth; h=canvas.clientHeight;
    if (!w || !h) return;
    const dpr=Math.min(devicePixelRatio||1,1.5);
    canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);
    ctx.setTransform(dpr,0,0,dpr,0,0);
    reference=makeView(TerrainCamera.standard);
    view=reference;
    buildSurface();
    backdrop=surface(canvas.width,canvas.height);
    const g=backdrop.getContext('2d');g.setTransform(dpr,0,0,dpr,0,0);
    paintFloor(g);
    wall=buildWall();
    floorLight=buildFloorLight();
    restingDots=null;
    draw();
    wake();
  }
  let lastShot=-1;
  function draw(now=performance.now()) {
    if (!backdrop || !rendering || document.hidden) return;
    ctx.globalAlpha=1;
    if (camera.auto) {
      view=makeView(camera.pose);
      if (camera.sequence!==lastShot) {
        floorLight.updated=wall.updated=-Infinity;
        lastShot=camera.sequence;
      }
      buildSurface();
      paintFloor(ctx);
    } else {
      ctx.drawImage(backdrop,0,0,w,h);
    }
    drawHaze(now);
    if (field.gain) drawFloorLight(now);
    drawWall(ctx,now);
    if (!field.gain && !camera.auto && !gpu?.ready) {
      if (!restingDots) {
        restingDots=surface(canvas.width,canvas.height);
        const dotsCtx=restingDots.getContext('2d');
        dotsCtx.setTransform(canvas.width/w,0,0,canvas.height/h,0,0);
        preparePoints(vertices);drawPoints(dotsCtx);
      }
      ctx.drawImage(restingDots,0,0,w,h);return;
    }
    preparePoints(gpu?.ready?rim:vertices);
    for (const p of rim) {
      if (p.lift<.5) continue;
      const heat=p.strength/(strengths-1), height=p.hover+p.lift;
      const top=p.y-height, width=p.spacing*(.60+.65*heat);
      ctx.globalAlpha=.1+.38*heat;
      ctx.drawImage(flare,p.tint*beamW,0,beamW,beamH,p.x-width/2,top,width,height);
      // Only the outer row has a vertical ruler from emitter to ground.
      ctx.globalAlpha=.30+.65*heat;
      ctx.fillStyle='rgb('+colors[p.tint]+')';
      const stroke=Math.max(.75,p.spacing*.055);
      ctx.fillRect(p.x-stroke*.5,top,stroke,height);
      ctx.globalAlpha=.25+.55*heat;
      ctx.fillStyle='#fffffb';
      ctx.fillRect(p.x-stroke*.22,top,stroke*.44,height);
    }
    ctx.globalAlpha=1;
    if (gpu?.ready) gpu.draw(vertices,view,w,h,canvas.width,canvas.height);
    else drawPoints(ctx);
  }
  function animate(now) {
    frame=0;
    if (!rendering || document.hidden) return;
    const elapsed=now-last, live=camera.auto || field.playing || field.gain>0;
    const interval=reduced.matches?(live?65:250):(live?1000/60:1000/24);
    if (now<nextDraw-.5) {frame=requestAnimationFrame(animate);return;}
    last=now;
    nextDraw+=interval;
    if (nextDraw<=now || nextDraw>now+interval+1) nextDraw=now+interval;
    field.advance(now,Math.min(250,elapsed));
    camera.advance(Math.min(250,elapsed));
    draw(now);
    frame=requestAnimationFrame(animate);
  }
  function wake() {
    if (rendering && !frame && !document.hidden) {
      nextDraw=performance.now();last=nextDraw-17;frame=requestAnimationFrame(animate);
    }
  }
  const cameraButton=document.getElementById('cameraMode');
  {
    const key='ac8.camera.auto';
    function setCamera(enabled) {
      camera.setAuto(enabled);
      if (cameraButton) {
        cameraButton.setAttribute('aria-pressed',String(enabled));
        cameraButton.title=enabled?'恢复正面固定视角':'开启随机运镜';
      }
      if (!backdrop) return;
      if (!enabled) {view=reference;buildSurface();}
      floorLight.updated=wall.updated=-Infinity;
      draw();wake();
    }
    let saved=false;
    try {saved=localStorage.getItem(key)==='1';} catch {}
    setCamera(saved);
    window.addEventListener('storage',event=>{if(event.key===key) setCamera(event.newValue==='1');});
    if (cameraButton) cameraButton.onclick=()=>{
      setCamera(!camera.auto);
      try {localStorage.setItem(key,camera.auto?'1':'0');} catch {}
    };
  }
  window.setSandTableEnabled=enabled=>{
    if (enabled===rendering) return;
    rendering=enabled;
    if (enabled) resize();
    else {cancelAnimationFrame(frame);frame=0;field.clear();}
  };
  window.updateSandTable=state=>{
    if (!rendering) return;
    camera.sync(state);
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
