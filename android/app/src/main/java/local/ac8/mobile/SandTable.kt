package local.ac8.mobile

import android.content.Context
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
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference

internal data class AudioSlice(val sequence:Long,val pcm:ByteArray)

internal object SandAudio {
    val receiver=AtomicReference<ArrayBlockingQueue<AudioSlice>?>(null)
    private val sequence=AtomicLong()
    fun reset(){sequence.addAndGet(100)}
    fun push(pcm:ByteArray){
        val queue=receiver.get() ?: return
        val slice=AudioSlice(sequence.incrementAndGet(),pcm)
        if(!queue.offer(slice)){queue.poll();queue.offer(slice)}
    }
}

/** Only the shared desktop drawing surface runs here. Compose owns the entire touch UI. */
internal class SandTableView(context:Context):WebView(context){
    private val origin="https://appassets.androidplatform.net/"
    private val files=setOf("sand-table.html","spectral-field.js","terrain-camera.js","terrain-points.js","terrain.js")
    private var loaded=false
    private var resumed=false
    private var playing=false
    private var inbox:ArrayBlockingQueue<AudioSlice>?=null
    private var worker:Thread?=null
    private var latest:String?=null
    private var inFlight=false
    private var disposed=false

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
                if(!disposed && url==origin+"sand-table.html"){loaded=true;sync()}
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
    fun playback(value:Boolean){if(playing!=value){playing=value;sync()}}
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
                    var previous=-1L
                    // PCM is only published after the playback service has started Python.
                    var analyzer:com.chaquo.python.PyObject?=null
                    while(!Thread.currentThread().isInterrupted){
                        val sample=source.take()
                        val fft=analyzer ?: Python.getInstance().getModule("mobile_spectrum").callAttr("Analyzer").also{analyzer=it}
                        val packet=fft.callAttr("packet",sample.pcm,previous+1!=sample.sequence).toString()
                        previous=sample.sequence
                        post {if(inbox===source && !disposed){latest=packet;deliver()}}
                    }
                }catch(_:InterruptedException){}catch(e:Exception){android.util.Log.e("AC8Terrain","Spectrum",e)}
            },"AC8 spectrum").apply{start()}
        }
    }
    private fun deliver(){
        if(inFlight || disposed || inbox==null)return
        val packet=latest ?: return
        inFlight=true;latest=null
        evaluateJavascript("(function(s){window.mobileEpoch=s.spectrumEpoch;window.updateSandTable(s)})($packet)"){
            inFlight=false;deliver()
        }
    }
    private fun detachAudio(){
        inbox?.let{SandAudio.receiver.compareAndSet(it,null)}
        inbox=null;latest=null;worker?.interrupt();worker=null
    }
    fun release(){
        disposed=true;detachAudio();stopLoading();destroy()
    }
}

@Composable internal fun SandTable(playing:Boolean,modifier:Modifier=Modifier){
    val context=LocalContext.current
    val view=remember(context){SandTableView(context)}
    val lifecycle=(LocalActivity.current as ComponentActivity).lifecycle
    DisposableEffect(lifecycle,view){
        val observer=LifecycleEventObserver{_,_->view.active(lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED))}
        lifecycle.addObserver(observer)
        onDispose{lifecycle.removeObserver(observer);view.active(false)}
    }
    AndroidView(factory={view},modifier=modifier,onRelease={it.release()},update={it.playback(playing)})
}
