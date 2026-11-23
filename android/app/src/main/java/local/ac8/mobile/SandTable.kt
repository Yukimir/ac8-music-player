package local.ac8.mobile

import android.content.Context
import android.hardware.display.DisplayManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.Process
import android.view.View
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.compose.LocalActivity
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import com.chaquo.python.Python
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.atomic.AtomicReference

internal object SandAudio {
    val history=TerrainAudioHistory()
    val receiver=AtomicReference<ArrayBlockingQueue<AudioSlice>?>(null)
    fun reset(){history.reset()}
    fun push(pcm:ByteArray){
        val slice=history.push(pcm)
        val queue=receiver.get() ?: return
        if(!queue.offer(slice)){queue.poll();queue.offer(slice)}
    }
}

/** Last-resort renderer. The desktop scripts can also draw the point field with Canvas 2D. */
internal class SandTableView(context:Context):WebView(context){
    private data class Packet(val json:String,val epoch:Long,val end:Long)
    private val origin="https://appassets.androidplatform.net/"
    private val files=setOf("sand-table.html","spectral-field.js","terrain-camera.js","terrain-points.js","terrain.js")
    private var loaded=false
    private var resumed=false
    private var playing=false
    private var inbox:ArrayBlockingQueue<AudioSlice>?=null
    private var worker:Thread?=null
    private var latest:Packet?=null
    private val delivered=AtomicReference<Packet?>()
    private var inFlight=false
    private var disposed=false
    private var clock=TerrainClock(-1,0,0,0,false)
    private var cruise=false

    init {
        setBackgroundColor(android.graphics.Color.rgb(3,10,21))
        importantForAccessibility=View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
        isFocusable=false;isClickable=false
        settings.javaScriptEnabled=true
        settings.allowFileAccess=false;settings.allowContentAccess=false
        webViewClient=object:WebViewClient(){
            override fun shouldOverrideUrlLoading(view:WebView,request:WebResourceRequest)=true
            override fun shouldInterceptRequest(view:WebView,request:WebResourceRequest):WebResourceResponse{
                val uri=request.url
                val name=uri.path?.removePrefix("/")
                if(uri.scheme=="https" && uri.host=="appassets.androidplatform.net" && name in files){
                    val mime=if(name=="sand-table.html")"text/html" else "application/javascript"
                    return WebResourceResponse(mime,"UTF-8",context.assets.open(requireNotNull(name)))
                }
                return WebResourceResponse("text/plain","UTF-8",404,"Not found",emptyMap(),null)
            }
            override fun onPageFinished(view:WebView,url:String){
                if(!disposed && url==origin+"sand-table.html"){loaded=true;cameraState();sync()}
            }
        }
        loadUrl(origin+"sand-table.html")
    }
    fun active(value:Boolean){
        if(disposed || resumed==value)return
        resumed=value
        if(value)onResume()
        sync()
        if(!value)onPause()
    }
    fun update(value:TerrainClock,auto:Boolean){
        if(value!=clock || auto!=cruise){clock=value;cruise=auto;if(loaded)cameraState()}
        if(playing!=value.playing){playing=value.playing;sync()}
    }
    private fun cameraState(){
        evaluateJavascript("window.setMobileCruise($cruise);window.mobileClock={song:${clock.song},clock:${clock.clock},gridOrigin:${clock.origin},bar:${clock.bar},sampleRate:48000}",null)
    }
    private fun sync(){
        if(!loaded || disposed)return
        evaluateJavascript("window.setSandTableEnabled && window.setSandTableEnabled($resumed)",null)
        if(!resumed || !playing){
            detachAudio()
            evaluateJavascript("window.updateSandTable && window.updateSandTable({playing:false,spectrumEpoch:window.mobileEpoch})",null)
        }else if(inbox==null){
            val source=ArrayBlockingQueue<AudioSlice>(4)
            inbox=source;SandAudio.receiver.set(source)
            worker=Thread({
                Process.setThreadPriority(Process.THREAD_PRIORITY_BACKGROUND)
                try {
                    val batch=TerrainAudioHistory.Batch()
                    var analyzerEpoch=-1L
                    // PCM is only published after the playback service has started Python.
                    var analyzer:com.chaquo.python.PyObject?=null
                    while(!Thread.currentThread().isInterrupted){
                        source.take()
                        SandAudio.history.collect(batch)
                        if(batch.count==0)continue
                        val fft=analyzer ?: Python.getInstance().getModule("mobile_spectrum").callAttr("Analyzer").also{analyzer=it}
                        val changed=analyzerEpoch!=batch.epoch
                        val after=delivered.get()?.takeIf{it.epoch==batch.epoch}?.end ?: -1L
                        for(i in 0 until batch.count-1)fft.callAttr("ingest",batch.pcm[i],changed && i==0)
                        val json=fft.callAttr("packet",batch.pcm[batch.count-1],changed && batch.count==1,after).toString()
                        val packet=Packet(json,batch.epoch,fft.get("block_count")!!.toLong())
                        analyzerEpoch=batch.epoch
                        post {if(inbox===source && !disposed){latest=packet;deliver()}}
                    }
                }catch(_:InterruptedException){}catch(e:Exception){android.util.Log.e("AC8Terrain","Spectrum",e)}
            },"AC8 spectrum").apply{start()}
        }
    }
    private fun deliver(){
        if(inFlight || disposed || inbox==null)return
        val packet=latest ?: return
        val source=inbox
        inFlight=true;latest=null
        evaluateJavascript("(function(s){window.mobileEpoch=s.spectrumEpoch;window.updateSandTable(Object.assign(s,window.mobileClock))})(${packet.json})"){
            if(inbox===source)delivered.set(packet)
            inFlight=false;deliver()
        }
    }
    private fun detachAudio(){
        inbox?.let{SandAudio.receiver.compareAndSet(it,null)}
        inbox=null;latest=null;delivered.set(null);worker?.interrupt();worker=null
    }
    fun release(){
        disposed=true;detachAudio();stopLoading();destroy()
    }
}

@Composable internal fun SandTable(clock:TerrainClock,cruise:Boolean,modifier:Modifier=Modifier){
    val context=LocalContext.current
    val activity=LocalActivity.current as ComponentActivity
    val lifecycle=activity.lifecycle
    var web by remember(context){mutableStateOf(false)}
    val view:View=remember(context,web){
        if(web) SandTableView(context)
        else NativeSandTable(context){android.util.Log.i("AC8Terrain","WebView fallback");web=true}
    }
    DisposableEffect(lifecycle,view){
        val window=activity.window
        val savedRate=window.attributes.preferredRefreshRate
        val savedMode=window.attributes.preferredDisplayModeId
        val decor=window.decorView
        val savedViewRate=if(Build.VERSION.SDK_INT>=35)decor.requestedFrameRate else 0f
        val displays=context.getSystemService(DisplayManager::class.java)
        fun rate(active:Boolean){
            val display=window.decorView.display ?: return
            val mode=display.mode
            val fastest=display.supportedModes.filter {
                it.physicalWidth==mode.physicalWidth && it.physicalHeight==mode.physicalHeight
            }.maxByOrNull {it.refreshRate} ?: mode
            val hz=if(active)fastest.refreshRate else savedRate
            val modeId=if(active)fastest.modeId else savedMode
            val attrs=window.attributes
            if(attrs.preferredRefreshRate!=hz || attrs.preferredDisplayModeId!=modeId)
                window.attributes=attrs.apply{preferredRefreshRate=hz;preferredDisplayModeId=modeId}
            if(Build.VERSION.SDK_INT>=35)decor.requestedFrameRate=if(active)hz else savedViewRate
            if(view is NativeSandTable)view.requestRate(if(active)hz else 0f)
        }
        fun sync(){
            val active=lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)
            rate(active)
            when(view){is NativeSandTable->view.enable(active);is SandTableView->view.active(active)}
        }
        val observer=LifecycleEventObserver{_,_->sync()}
        val listener=object:DisplayManager.DisplayListener {
            override fun onDisplayChanged(id:Int){if(id==window.decorView.display?.displayId)rate(lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED))}
            override fun onDisplayAdded(id:Int){}
            override fun onDisplayRemoved(id:Int){}
        }
        displays.registerDisplayListener(listener,Handler(Looper.getMainLooper()))
        lifecycle.addObserver(observer);sync()
        onDispose{displays.unregisterDisplayListener(listener);lifecycle.removeObserver(observer);rate(false)}
    }
    key(view){
        AndroidView(factory={view},modifier=modifier,onRelease={
            when(it){is NativeSandTable->it.release();is SandTableView->it.release()}
        },update={when(it){is NativeSandTable->it.update(clock,cruise);is SandTableView->it.update(clock,cruise)}})
    }
}
