@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class, androidx.compose.material3.ExperimentalMaterial3ExpressiveApi::class)
package local.ac8.mobile

import android.os.Bundle
import android.os.Build
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.*
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import org.json.JSONObject

internal fun glyph(name:String,data:String)=ImageVector.Builder(name,24.dp,24.dp,24f,24f).apply {
    addPath(PathParser().parsePathString(data).toNodes(),fill=SolidColor(Color.Black))
}.build()
private val libraryIcon=glyph("曲库","M4 6H2v15h15v-2H4z M7 2h15v15H7z M17 5v5.3a2.5 2.5 0 1 0 1.5 2.3V7h2V5z")
private val playingIcon=glyph("正在播放","M3 10h3v9H3z M8 4h3v15H8z M13 7h3v12h-3z M18 11h3v8h-3z")
private val linkIcon=glyph("传输","M2 4h15v2H4v10h7v2H2z M13 8h9v14h-9z M15 10v9h5v-9z")
internal val playIcon=glyph("播放","M7 3v18l14-9z")
internal val pauseIcon=glyph("暂停","M5 4h5v16H5z M14 4h5v16h-5z")
internal val previousIcon=glyph("上一首","M5 5h3v14H5z M19 5v14L9 12z")
internal val nextIcon=glyph("下一首","M16 5h3v14h-3z M5 5l10 7-10 7z")

class MainActivity:ComponentActivity(){
    override fun onCreate(savedInstanceState:Bundle?){
        super.onCreate(savedInstanceState);enableEdgeToEdge()
        val app=application as PlayerApp
        setContent {
            val dark=isSystemInDarkTheme();val context=LocalContext.current
            val colors=when {
                Build.VERSION.SDK_INT>=31 && dark->dynamicDarkColorScheme(context)
                Build.VERSION.SDK_INT>=31->dynamicLightColorScheme(context)
                dark->darkColorScheme(primary=Color(0xFFA3CAFF))
                else->lightColorScheme(primary=Color(0xFF225E99))
            }
            MaterialExpressiveTheme(colorScheme=colors){PlayerScreen(app)}
        }
    }
}

@Composable private fun PlayerScreen(app:PlayerApp){
    val revision by app.library.revision.collectAsState()
    val now by MusicService.now.collectAsState()
    val entries by app.library.entries.collectAsState()
    val playlist by app.library.playlist.collectAsState()
    val mode by app.library.mode.collectAsState()
    var browsePlaylist by rememberSaveable{mutableStateOf(false)}
    var page by rememberSaveable{mutableIntStateOf(0)}
    val names=listOf("曲库","正在播放","传输")
    val icons=listOf(libraryIcon,playingIcon,linkIcon)
    fun selectMode(mode:PlayMode){
        app.library.setMode(mode)
        if(now.data.has("song"))MusicService.send(app,"mode")
    }
    fun selectRow(row:LibraryEntry){
        MusicService.send(app,"select",song=row.song.id,plan=row.edition.id,entry=row.id,playlist=browsePlaylist)
        page=1
    }
    if(page==1){
        TerrainPlayer(now,onBack={page=0},openTransfer={page=2},
            openQueue={if(now.data.has("song"))browsePlaylist=now.data.optBoolean("playlist")},
            transport={MusicService.send(app,it)},
            settings={
                ArrangementPanel(app,now,revision){id,plan->
                    MusicService.send(app,"select",song=id,plan=plan,entry=now.data.optString("entry"),
                        playlist=now.data.optString("plan")==plan && now.data.optBoolean("playlist"))
                }
            },queue={close->
                SongLibrary(if(browsePlaylist)playlist else entries,browsePlaylist,{browsePlaylist=it},mode,::selectMode,
                    now.data,close,showCurrent=false){row->selectRow(row);close()}
            })
        return
    }
    Scaffold(topBar={TopAppBar(title={Text(names[page])})},bottomBar={
        NavigationBar {names.forEachIndexed{i,n->
            NavigationBarItem(selected=page==i,onClick={page=i},icon={Icon(icons[i],null)},label={Text(n)})
        }}
    }){inner->
        Box(Modifier.fillMaxSize().padding(inner).padding(horizontal=18.dp)){
            when(page){
                0->if(entries.isEmpty())Empty("手机曲库还是空的","从电脑选择曲目和编排，传过来就能离线听。","连接电脑"){page=2}
                   else SongLibrary(if(browsePlaylist)playlist else entries,browsePlaylist,{browsePlaylist=it},mode,::selectMode,
                       now.data,{page=1},select=::selectRow)
                else->TransferPage(app)
            }
        }
    }
}

@Composable private fun SongLibrary(rows:List<LibraryEntry>,playlist:Boolean,source:(Boolean)->Unit,mode:PlayMode,
    selectMode:(PlayMode)->Unit,now:JSONObject,openPlayer:()->Unit,showCurrent:Boolean=true,select:(LibraryEntry)->Unit){
    var query by rememberSaveable{mutableStateOf("")}
    val visible=remember(rows,query){rows.filter{it.song.title.contains(query,true) || it.song.origin.contains(query,true) || it.detail.contains(query,true)}}
    val playing=now.optBoolean("playing")
    val currentSong=now.optLong("song",-1);val plan=now.optString("plan")
    val status=when{playing->"正在播放";now.optBoolean("finished")->"已结束";else->"已暂停"}
    LazyColumn(verticalArrangement=Arrangement.spacedBy(2.dp)){
        item {
            Column(verticalArrangement=Arrangement.spacedBy(8.dp)){
                Row(horizontalArrangement=Arrangement.spacedBy(8.dp)){
                    FilterChip(!playlist,{source(false)},label={Text("全部曲目")})
                    FilterChip(playlist,{source(true)},label={Text("电脑播放列表")})
                }
                PlaybackModeMenu(mode,selectMode)
                if(showCurrent && currentSong>=0)TextButton(onClick=openPlayer){
                    Text(status+" · "+now.optString("title")+" · "+now.optString("editionName"),maxLines=2,overflow=TextOverflow.Ellipsis)
                }
                OutlinedTextField(query,{query=it},label={Text("搜索曲名、编排或出处")},singleLine=true,modifier=Modifier.fillMaxWidth())
                if(visible.isEmpty())Text(if(playlist && rows.isEmpty())"在电脑传输窗口选择「播放列表」，勾选并发送即可。" else if(rows.isEmpty())"手机曲库还是空的，连接电脑后即可传入曲目。" else "没有匹配的曲目")
            }
        }
        items(visible,key={it.id}){row->
            val active=row.song.id==currentSong && row.edition.id==plan &&
                (!playlist || now.optBoolean("playlist") && row.id==now.optString("entry"))
            ListItem(headlineContent={Text(row.song.title,maxLines=2,overflow=TextOverflow.Ellipsis)},
                supportingContent={Column{
                    Text(row.detail)
                    if(row.song.origin.isNotBlank())Text(row.song.origin,style=MaterialTheme.typography.labelSmall,maxLines=1,overflow=TextOverflow.Ellipsis)
                }},trailingContent={
                    if(active)Text(status,color=MaterialTheme.colorScheme.primary)
                    else Icon(playIcon,"播放 "+row.song.title+" · "+row.edition.name,Modifier.size(18.dp))
                },colors=ListItemDefaults.colors(containerColor=if(active)MaterialTheme.colorScheme.secondaryContainer else MaterialTheme.colorScheme.surface),
                modifier=Modifier.clickable{if(active && now.optBoolean("playlist")==playlist && !now.optBoolean("finished"))openPlayer() else select(row)})
        }
    }
}

@Composable private fun Empty(title:String,body:String,button:String,act:()->Unit){
    Box(Modifier.fillMaxSize(),contentAlignment=Alignment.Center){
        Column(verticalArrangement=Arrangement.spacedBy(14.dp),modifier=Modifier.fillMaxWidth().padding(14.dp)){
            Text(title,style=MaterialTheme.typography.headlineSmall);Text(body,color=MaterialTheme.colorScheme.onSurfaceVariant)
            Button(onClick=act){Text(button)}
        }
    }
}

@Composable private fun TransferPage(app:PlayerApp){
    val state by app.link.state.collectAsState()
    var paste by rememberSaveable{mutableStateOf(false)}
    var code by rememberSaveable{mutableStateOf("")}
    val scan=rememberLauncherForActivityResult(ScanContract()){r->r.contents?.let(app.link::connect)}
    Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()),verticalArrangement=Arrangement.spacedBy(16.dp)){
        Card(Modifier.fillMaxWidth(),colors=CardDefaults.cardColors(containerColor=MaterialTheme.colorScheme.secondaryContainer)) {
            Column(Modifier.padding(20.dp),verticalArrangement=Arrangement.spacedBy(8.dp)){
                Icon(linkIcon,null)
                Text(state.text,style=MaterialTheme.typography.titleLarge)
                if(state.host.isNotBlank())Text(state.host)
                state.progress?.let { LinearProgressIndicator(progress={it},modifier=Modifier.fillMaxWidth()) }
            }
        }
        Text("电脑播放器右上角打开「手机传输与备份」，点击「展示连接二维码」。两台设备连接同一 Wi-Fi 后即可扫码。")
        if(state.active)OutlinedButton(onClick=app.link::disconnect){Text("断开连接")}
        else {
            Button(onClick={scan.launch(ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                .setPrompt("扫描电脑播放器的配对二维码").setBeepEnabled(false).setOrientationLocked(false))}){Text("扫码连接")}
            TextButton(onClick={paste=true}){Text("输入配对码")}
        }
    }
    if(paste)AlertDialog(onDismissRequest={paste=false},title={Text("输入配对码")},text={
        OutlinedTextField(code,{code=it},label={Text("电脑上复制的配对码")},maxLines=5)
    },confirmButton={TextButton(enabled=code.isNotBlank(),onClick={app.link.connect(code.trim());code="";paste=false}){Text("连接")}},
        dismissButton={TextButton(onClick={paste=false}){Text("取消")}})
}
internal fun trackTime(frames:Long):String{val s=frames.coerceAtLeast(0)/48000;return "%d:%02d".format(s/60,s%60)}

@Composable private fun PlaybackModeMenu(mode:PlayMode,select:(PlayMode)->Unit){
    ValueMenu("播放模式",mode.name,PlayMode.entries.map{it.name to it.label}){select(PlayMode.valueOf(it))}
}
@Composable private fun ArrangementPanel(app:PlayerApp,now:Playing,revision:Int,select:(Long,String)->Unit){
    val s=now.data
    if(!s.has("song"))return
    val id=s.getLong("song")
    val editions=remember(id,revision){app.library.plans(id)}
    val interactive=editions.isNotEmpty();val free=s.optString("plan")=="free" && interactive
    fun command(op:String,v:String=""){MusicService.send(app,op,v)}
    val count=s.optInt("repeat")
    val parts=s.optJSONArray("parts")
    LazyColumn(Modifier.fillMaxWidth(),verticalArrangement=Arrangement.spacedBy(14.dp)){
        item {
            Column(verticalArrangement=Arrangement.spacedBy(14.dp)){
                if(now.error.isNotEmpty())Text(now.error,color=MaterialTheme.colorScheme.error)
                if(interactive){
                    val current=s.optString("plan")
                    ValueMenu("编排",current,listOf("free" to "自由编排")+editions.map{it.id to it.name}){select(id,it)}
                    val position=if(s.optBoolean("bridge"))"段落过渡中" else if(free)"段落" else "编排 · 第 "+(s.optInt("stepIndex")+1)+" / "+(parts?.length() ?: 0)+" 段"
                    Text(position+" · 第 "+s.optInt("run")+" / "+(if(count==0)"∞" else count.toString())+" 遍",style=MaterialTheme.typography.titleMedium)
                }
            }
        }
        if(interactive && parts!=null){
            items(parts.length()){i->
                val p=parts.getJSONObject(i);val key=p.getLong("id")
                val active=!s.optBoolean("bridge") && if(free)key==s.getLong("segment") else i==s.optInt("stepIndex")
                Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically){
                    val label=p.getString("label")
                    val title=if(free)label else "${i+1}. $label"
                    Text((if(active)"▶  " else "")+title+(if(free && s.optLong("queued")==key)" · 已排队" else ""),Modifier.weight(1f),
                        color=if(active)MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface)
                    if(free){TextButton(onClick={command("queue",key.toString())}){Text("排队")}
                        TextButton(onClick={command("jump",key.toString())}){Text("立即切换")}}
                    else {
                        val repeats=p.optInt("repeat",1)
                        Text((if(repeats==0)"∞ 遍" else "$repeats 遍")+
                            (if(p.isNull("bars"))"" else " · 每遍 "+p.getInt("bars")+" 小节"),style=MaterialTheme.typography.labelMedium)
                    }
                }
            }
        }
        item {
            Column(verticalArrangement=Arrangement.spacedBy(14.dp)){
                if(interactive){
                    if(!free && !s.isNull("loopFrom"))Text("末尾返回第 "+(s.getInt("loopFrom")+1)+" 段循环")
                    if(free){
                        ValueMenu("循环次数",count.toString(),listOf(0,1,2,4,8,16).map{it.toString() to if(it==0)"无限" else it.toString()}){command("repeat",it)}
                        if(s.optInt("bar")>0)ValueMenu("每遍长度",s.optInt("bars").toString(),
                            listOf(0,1,2,4,8,16,32,64,128).filter{it<=s.optInt("maxBars")}.map{it.toString() to if(it==0)"完整段落" else "$it 小节"}){command("bars",it)}
                    }
                    Text("分轨",style=MaterialTheme.typography.titleMedium)
                    val lanes=s.getJSONArray("lanes")
                    FlowRow(horizontalArrangement=Arrangement.spacedBy(8.dp)){
                        for(i in 0 until lanes.length()){
                            val lane=lanes.getJSONObject(i)
                            FilterChip(selected=lane.getBoolean("on"),onClick={command("lane",lane.getString("key"))},enabled=free,label={Text("音轨 "+(i+1))})
                        }
                    }
                }
                if(app.library.hasEffects()){
                    CockpitPanel(app.cockpit,s.optString("effectsError"))
                }
                TextButton(onClick={command("stop")}){Text("停止播放")}
                Spacer(Modifier.height(8.dp))
            }
        }
    }
}
@Composable private fun CockpitPanel(controls:CockpitControls,error:String){
    val options by controls.state.collectAsState()
    HorizontalDivider()
    Text("座舱音效",style=MaterialTheme.typography.titleMedium)
    CockpitSwitch("随机音效",options.random,controls::random)
    FlowRow(horizontalArrangement=Arrangement.spacedBy(8.dp)){
        for((key,label) in sceneLabels){
            FilterChip(selected=key in options.categories,onClick={controls.toggle(key)},label={Text(label)})
        }
    }
    CockpitSwitch("发动机底噪",options.engine,controls::engine)
    if(error.isNotBlank())Text(error,color=MaterialTheme.colorScheme.error)
    Text("音效音量")
    Slider(value=options.volume,onValueChange=controls::volume,onValueChangeFinished=controls::saveVolume,valueRange=0f..2f)
}
@Composable private fun CockpitSwitch(label:String,on:Boolean,change:(Boolean)->Unit){
    Row(Modifier.fillMaxWidth().toggleable(value=on,role=Role.Switch,onValueChange=change)
        .padding(vertical=4.dp),verticalAlignment=Alignment.CenterVertically){
        Text(label,Modifier.weight(1f));Switch(on,onCheckedChange=null)
    }
}
@Composable private fun ValueMenu(label:String,current:String,options:List<Pair<String,String>>,pick:(String)->Unit){
    var expanded by remember{mutableStateOf(false)}
    Row(verticalAlignment=Alignment.CenterVertically){
        Text(label,Modifier.weight(1f));Box{
            FilledTonalButton(onClick={expanded=true}){Text(options.firstOrNull{it.first==current}?.second ?: current)}
            DropdownMenu(expanded,{expanded=false}){options.forEach{(k,v)->DropdownMenuItem(text={Text(v)},onClick={expanded=false;pick(k)})}}
        }
    }
}
