package local.ac8.mobile

import android.content.Context
import android.content.res.AssetManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.Process
import android.util.Log
import android.view.Choreographer
import android.view.Surface
import android.view.SurfaceHolder
import android.view.SurfaceView
import java.util.concurrent.CountDownLatch
import kotlin.math.*

internal object TerrainGpu {
    private val available=runCatching { System.loadLibrary("ac8terrain") }.isSuccess
    private val vulkan by lazy {runCatching {System.loadLibrary("ac8vulkan")}.isSuccess}
    fun open(surface:Surface,assets:AssetManager,backend:Int):Long {
        if(!available)return 0
        return if(backend==0){if(vulkan)openVulkan(surface) else 0} else openGL(surface,assets)
    }
    private external fun openGL(surface: Surface, assets: AssetManager): Long
    private external fun openVulkan(surface: Surface): Long
    external fun draw(handle: Long, params: FloatArray, pcm:Array<ByteArray?>, count:Int, clear:Boolean): Int
    external fun close(handle: Long)
}

// The render thread owns the surface, GPU context, audio history and camera clock.
internal class NativeSandTable(context: Context, private val fallback: ()->Unit): SurfaceView(context), SurfaceHolder.Callback {
    private val thread=HandlerThread("AC8 sand table",Process.THREAD_PRIORITY_DISPLAY).apply{start()}
    private val handler=Handler(thread.looper)
    private val camera=TerrainCruise()
    private val params=FloatArray(16)
    private val batch=TerrainAudioHistory.Batch()
    private val scroll=TerrainScrollClock()
    private var target: Surface?=null
    private var handle=0L
    private var backend=0
    private var active=false
    private var cruise=false
    private var state=TerrainClock(-1,0,0,0,false)
    private var requested=false
    private var lastTick=0L
    private var animationTime=0.0
    private var columns=28f
    private var surfaceWidth=0
    private var surfaceHeight=0
    private var disposed=false  // UI thread only
    private var recreateAttempts=0
    private var rate=0f
    private val tick=Choreographer.FrameCallback(::frame)

    init { holder.addCallback(this); isFocusable=false; importantForAccessibility=IMPORTANT_FOR_ACCESSIBILITY_NO }
    override fun onSizeChanged(w:Int,h:Int,oldw:Int,oldh:Int){
        super.onSizeChanged(w,h,oldw,oldh)
        if(w>0 && h>0){
            // Keep the overlaid Android controls at the display's native resolution.
            val scale=min(1.0,1600.0/max(w,h))
            holder.setFixedSize(max(1,(w*scale).roundToInt()),max(1,(h*scale).roundToInt()))
            val logicalWidth=w/resources.displayMetrics.density
            handler.post {columns=(12+logicalWidth/25).roundToInt().coerceIn(22,76).toFloat();wake()}
        }
    }
    fun requestRate(hz:Float){
        rate=hz
        val s=holder.surface
        if(Build.VERSION.SDK_INT>=30 && s.isValid)runCatching {s.setFrameRate(hz,Surface.FRAME_RATE_COMPATIBILITY_DEFAULT)}
    }
    fun enable(value:Boolean){if(!disposed)handler.post {
        active=value
        if(!value){cancelFrame();lastTick=0}
        else wake()
    }}
    fun update(clock:TerrainClock,auto:Boolean){if(!disposed)handler.post {
        state=clock;cruise=auto
        wake()
    }}
    private fun wake(){
        if(active && target!=null && backend<2 && !requested){requested=true;Choreographer.getInstance().postFrameCallback(tick)}
    }
    private fun cancelFrame(){if(requested)Choreographer.getInstance().removeFrameCallback(tick);requested=false}
    private fun closeGpu(){if(handle!=0L){TerrainGpu.close(handle);handle=0};batch.rewind()}
    private fun frame(time:Long){
        requested=false
        val surface=target ?: return
        if(!active || !surface.isValid)return
        while(handle==0L && backend<2){
            handle=TerrainGpu.open(surface,context.assets,backend)
            if(handle==0L)backend++
        }
        if(handle==0L){post {if(!disposed)fallback()};return}
        val dt=if(lastTick==0L)0.0 else ((time-lastTick)/1e6).coerceIn(0.0,250.0)
        lastTick=time;animationTime+=dt
        SandAudio.history.collect(batch)
        if(batch.newEpoch)camera.resetClock()
        val latest=if(batch.count>0)batch.pcm[batch.count-1] else null
        camera.pose(params,cruise,state,time/1e6,dt,latest)
        params[10]=(animationTime*.001%10000).toFloat()
        params[11]=columns
        params[14]=scroll.delay(batch.through,dt,batch.newEpoch)
        params[15]=1f
        val status=TerrainGpu.draw(handle,params,batch.pcm,batch.count,batch.newEpoch)
        if(status!=0){
            closeGpu()
            if(status<0 || ++recreateAttempts>2){backend++;recreateAttempts=0}
            // Let the next frame try the next renderer, including WebView if both failed.
            if(backend>=2){post {if(!disposed)fallback()};return}
        }else recreateAttempts=0
        if(cruise || state.playing || status!=0)wake()
    }
    override fun surfaceCreated(owner:SurfaceHolder){}
    override fun surfaceChanged(owner:SurfaceHolder,format:Int,width:Int,height:Int){
        val s=owner.surface
        requestRate(rate)
        if(!disposed)handler.post {
            if(target!==s || width!=surfaceWidth || height!=surfaceHeight){
                closeGpu();surfaceWidth=width;surfaceHeight=height
            }
            target=s;wake()
        }
    }
    private fun stopSurface(){
        val stopped=CountDownLatch(1)
        handler.post {try{cancelFrame();closeGpu();target=null;lastTick=0}finally{stopped.countDown()}}
        stopped.await()
    }
    override fun surfaceDestroyed(owner:SurfaceHolder){if(!disposed)stopSurface()}
    fun release(){
        if(disposed)return
        disposed=true;holder.removeCallback(this);stopSurface();thread.quitSafely()
    }
}
