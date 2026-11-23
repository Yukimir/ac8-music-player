package local.ac8.mobile

import android.app.*
import android.content.*
import android.graphics.Bitmap
import android.graphics.Canvas
import android.media.*
import android.media.session.MediaSession
import android.media.session.PlaybackState
import android.os.*
import com.chaquo.python.Python
import com.chaquo.python.PyObject
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.flow.MutableStateFlow
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.atomic.AtomicBoolean

data class Playing(val data:JSONObject=JSONObject(),val error:String="")
class MusicService:Service(){
    companion object {
        private const val STOP_ACTION="local.ac8.mobile.STOP"
        val now=MutableStateFlow(Playing())
        fun send(c:Context,op:String,value:String="",song:Long=0,plan:String="default",entry:String?=null,playlist:Boolean=false){
            val i=Intent(c,MusicService::class.java).setAction(op).putExtra("value",value).putExtra("song",song).putExtra("plan",plan)
                .putExtra("entry",entry).putExtra("playlist",playlist)
            if(op=="select")c.startForegroundService(i) else c.startService(i)
        }
    }
    private val commands=LinkedBlockingQueue<Intent>()
    private val alive=AtomicBoolean(true)
    private lateinit var session:MediaSession
    private lateinit var manager:AudioManager
    private lateinit var focus:AudioFocusRequest
    private val openPlayer by lazy {PendingIntent.getActivity(this,0,Intent(this,MainActivity::class.java)
        .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP),PendingIntent.FLAG_IMMUTABLE)}
    private val artwork by lazy {
        Bitmap.createBitmap(256,256,Bitmap.Config.ARGB_8888).also { bitmap->
            applicationInfo.loadIcon(packageManager).apply {setBounds(0,0,bitmap.width,bitmap.height);draw(Canvas(bitmap))}
        }
    }
    private val attrs=AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build()
    private val noisy=object:BroadcastReceiver(){override fun onReceive(c:Context,i:Intent){commands.offer(Intent("pause"))}}
    override fun onCreate(){
        super.onCreate();manager=getSystemService(AudioManager::class.java)
        focus=AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN).setAudioAttributes(attrs)
            .setOnAudioFocusChangeListener{if(it<0)commands.offer(Intent("pause"))}.build()
        getSystemService(NotificationManager::class.java).createNotificationChannel(NotificationChannel("play","音乐播放",NotificationManager.IMPORTANCE_LOW))
        session=MediaSession(this,"AC8").apply {
            setSessionActivity(openPlayer)
            setPlaybackToLocal(attrs)
            setCallback(object:MediaSession.Callback(){
                override fun onPlay(){commands.offer(Intent("resume"))}
                override fun onPause(){commands.offer(Intent("pause"))}
                override fun onSkipToNext(){commands.offer(Intent("next"))}
                override fun onSkipToPrevious(){commands.offer(Intent("previous"))}
                override fun onStop(){commands.offer(Intent("stop"))}
                override fun onCustomAction(action:String,extras:Bundle?){if(action==STOP_ACTION)onStop()}
            })
        }
        registerReceiver(noisy,IntentFilter(AudioManager.ACTION_AUDIO_BECOMING_NOISY))
        Thread(::audioWorker,"AC8 audio").start()
    }
    private fun notifyTrack(title:String,playing:Boolean,origin:String="",previous:Boolean=false,next:Boolean=false){
        fun pending(op:String)=PendingIntent.getService(this,op.hashCode(),Intent(this,MusicService::class.java).setAction(op),PendingIntent.FLAG_IMMUTABLE)
        val meta=MediaMetadata.Builder().putString(MediaMetadata.METADATA_KEY_TITLE,title)
            .putString(MediaMetadata.METADATA_KEY_ARTIST,origin).putBitmap(MediaMetadata.METADATA_KEY_ALBUM_ART,artwork).build()
        session.setMetadata(meta)
        session.setPlaybackState(PlaybackState.Builder().setActions(PlaybackState.ACTION_PLAY or PlaybackState.ACTION_PAUSE or
            PlaybackState.ACTION_PLAY_PAUSE or PlaybackState.ACTION_STOP or
            (if(next)PlaybackState.ACTION_SKIP_TO_NEXT else 0L) or (if(previous)PlaybackState.ACTION_SKIP_TO_PREVIOUS else 0L))
            .addCustomAction(STOP_ACTION,"停止",R.drawable.ic_stop)
            .setState(if(playing)PlaybackState.STATE_PLAYING else PlaybackState.STATE_PAUSED,
                PlaybackState.PLAYBACK_POSITION_UNKNOWN,if(playing)1f else 0f).build())
        session.isActive=true
        val n=Notification.Builder(this,"play").setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle(title).setContentText(origin).setContentIntent(openPlayer).setLargeIcon(artwork)
            .setCategory(Notification.CATEGORY_TRANSPORT).setVisibility(Notification.VISIBILITY_PUBLIC).setOnlyAlertOnce(true)
            .addAction(android.R.drawable.ic_media_previous,"上一首",pending("previous"))
            .addAction(if(playing)android.R.drawable.ic_media_pause else android.R.drawable.ic_media_play,if(playing)"暂停" else "播放",pending(if(playing)"pause" else "resume"))
            .addAction(android.R.drawable.ic_media_next,"下一首",pending("next"))
            .addAction(R.drawable.ic_stop,"停止",pending("stop"))
            .setStyle(Notification.MediaStyle().setMediaSession(session.sessionToken).setShowActionsInCompactView(0,1,2)).build()
        startForeground(16,n)
    }
    override fun onStartCommand(intent:Intent?,flags:Int,startId:Int):Int{
        if(intent!=null){
            if(intent.action=="select")notifyTrack("正在载入配乐",false)
            commands.offer(intent)
        }
        return START_NOT_STICKY
    }
    private fun audioWorker(){
        var output:AudioTrack?=null
        var player:PyObject?=null
        val wake=getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"AC8:Playback")
        try{
            android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_AUDIO)
            if(!Python.isStarted())Python.start(AndroidPlatform(applicationContext))
            val app=application as PlayerApp
            val lib=app.library
            val py=Python.getInstance().getModule("mobile_player").callAttr("Player",lib.root.path)
            player=py
            val size=AudioTrack.getMinBufferSize(48000,AudioFormat.CHANNEL_OUT_STEREO,AudioFormat.ENCODING_PCM_16BIT)
            val track=AudioTrack.Builder().setAudioAttributes(attrs).setAudioFormat(AudioFormat.Builder().setSampleRate(48000)
                .setChannelMask(AudioFormat.CHANNEL_OUT_STEREO).setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                .setTransferMode(AudioTrack.MODE_STREAM).setBufferSizeInBytes(maxOf(16384,size*2)).build()
            output=track
            var playing=false;var lastPoll=0L;var lastTitle="";var current=0L
            val queue=PlaybackQueue()
            var fromPlaylist=false
            var currentPlan="default"
            var editionName=""
            var parts=JSONArray()
            var lastPrevious=false;var lastNext=false;var lastEntry:String?=null
            var indexedRows:List<LibraryEntry>?=null
            var byId=emptyMap<String,LibraryEntry>()
            var ids=emptyList<String>()
            fun updateQueueEntries(){
                val rows=if(fromPlaylist)lib.playlist.value else lib.entries.value
                if(indexedRows!==rows){
                    indexedRows=rows;byId=rows.associateBy{it.id};ids=rows.map{it.id}
                }
            }
            var applied:CockpitOptions?=null
            var appliedRevision=-1
            fun select(sid:Long,plan:String){
                if(track.playState==AudioTrack.PLAYSTATE_PLAYING)track.pause()
                track.flush()
                py.callAttr("select",sid,plan)
                parts=JSONArray(py.callAttr("sections").toString())
                SandAudio.reset()
                current=sid;currentPlan=plan
                editionName=if(plan=="free")"自由编排" else lib.entries.value.firstOrNull{it.song.id==sid && it.edition.id==plan}?.edition?.name.orEmpty()
            }
            fun next(delta:Int):Boolean{
                val key=queue.neighbor(ids,delta) ?: return false
                val target=byId.getValue(key)
                select(target.song.id,target.edition.id)
                queue.moved(key,delta,ids);return true
            }
            while(alive.get()){
                val cmd=if(playing)commands.poll() else commands.take()
                if(!alive.get() || cmd?.action=="stop")break
                try{
                    val mode=lib.mode.value
                    if(queue.mode!=mode){
                        queue.mode=mode;py.callAttr("repeat_one",mode==PlayMode.RepeatOne)
                    }
                    updateQueueEntries()
                    val entry=byId[queue.current]
                    if(fromPlaylist && (entry?.song?.id!=current || entry.edition.id!=currentPlan)){
                        fromPlaylist=false;queue.start(catalogId(current,currentPlan));updateQueueEntries()
                    }
                    if(cmd!=null){
                        when(val op=cmd.action){
                            "select"->{
                                val song=cmd.getLongExtra("song",0);val plan=cmd.getStringExtra("plan") ?: "default"
                                select(song,plan)
                                val entry=cmd.getStringExtra("entry")
                                fromPlaylist=cmd.getBooleanExtra("playlist",false) && lib.playlist.value.any{it.id==entry && it.song.id==song && it.edition.id==plan}
                                queue.start(if(fromPlaylist)entry else catalogId(song,if(plan=="free")"default" else plan))
                                updateQueueEntries()
                            }
                            "previous"->next(-1)
                            "next"->next(1)
                            "mode"->Unit
                            else->py.callAttr("action",op,cmd.getStringExtra("value") ?: "")
                        }
                    }
                    val options=app.cockpit.state.value
                    val revision=lib.revision.value
                    if(options!==applied || revision!=appliedRevision){
                        val installed=lib.hasEffects()
                        py.callAttr("configure_effects",installed && options.random,installed && options.engine,
                            options.categories.joinToString(","),options.volume.toDouble())
                        applied=options;appliedRevision=revision
                    }
                    if(cmd!=null || SystemClock.elapsedRealtime()-lastPoll>=160){
                        var state=JSONObject(py.callAttr("state").toString())
                        if(playing && cmd?.action!="pause" && state.optBoolean("finished") && next(1))state=JSONObject(py.callAttr("state").toString())
                        var active=state.optBoolean("playing")
                        if(active && !playing && manager.requestAudioFocus(focus)!=AudioManager.AUDIOFOCUS_REQUEST_GRANTED){
                            py.callAttr("action","pause","");state=JSONObject(py.callAttr("state").toString());active=false
                        }
                        if(active && track.playState!=AudioTrack.PLAYSTATE_PLAYING)track.play()
                        if(!active && playing){track.pause();track.flush();manager.abandonAudioFocusRequest(focus)}
                        if(active && !wake.isHeld)wake.acquire()
                        if(!active && wake.isHeld)wake.release()
                        val title=state.optString("title")
                        val previous=queue.canMove(ids,-1);val following=queue.canMove(ids,1)
                        state.put("entry",queue.current).put("playlist",fromPlaylist).put("parts",parts).put("editionName",editionName)
                            .put("previous",previous).put("next",following)
                        if(title.isNotEmpty() && (active!=playing || title!=lastTitle || cmd?.action=="select" ||
                                previous!=lastPrevious || following!=lastNext || queue.current!=lastEntry)){
                            notifyTrack(title,active,editionName,previous,following)
                        }
                        lastPrevious=previous;lastNext=following;lastEntry=queue.current
                        playing=active;lastTitle=title;now.value=Playing(state);lastPoll=SystemClock.elapsedRealtime()
                    }
                    if(playing){
                        val block=py.callAttr("render",2048).toJava(ByteArray::class.java)
                        check(track.write(block,0,block.size,AudioTrack.WRITE_BLOCKING)==block.size){"音频输出中断"}
                        SandAudio.push(block)
                    }
                }catch(e:Exception){
                    py.callAttr("action","pause","");track.pause();track.flush();playing=false
                    manager.abandonAudioFocusRequest(focus)
                    if(wake.isHeld)wake.release()
                    now.value=Playing(JSONObject(py.callAttr("state").toString()).put("parts",parts)
                        .put("entry",queue.current).put("playlist",fromPlaylist),e.message.orEmpty())
                    if(lastTitle.isNotEmpty())notifyTrack(lastTitle,false)
                }
            }
        }catch(e:Exception){now.value=Playing(error=e.message ?: "播放失败");android.util.Log.e("AC8Audio","Audio",e)}
        finally{
            if(wake.isHeld)wake.release()
            output?.release();manager.abandonAudioFocusRequest(focus)
            try{player?.callAttr("close")}finally{stopSelf()}
        }
    }
    override fun onDestroy(){
        alive.set(false);commands.offer(Intent("stop"));unregisterReceiver(noisy)
        session.isActive=false;session.release();stopForeground(STOP_FOREGROUND_REMOVE)
        if(now.value.error.isEmpty())now.value=Playing()
        super.onDestroy()
    }
    override fun onBind(intent:Intent?)=null
}
