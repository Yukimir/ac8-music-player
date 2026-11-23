package local.ac8.mobile

import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.LocalActivity
import androidx.compose.animation.*
import androidx.compose.animation.core.tween
import androidx.compose.foundation.*
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.paneTitle
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.view.WindowCompat

private val frost=Color(0xFFD7E5ED)
private val muted=Color(0xFFACBECF)
private val night=Color(0xFF091522)
private val gear=glyph("播放设置","M19.43 12.98a7.8 7.8 0 0 0 0-1.96l2.11-1.65-2-3.46-2.49 1a7.7 7.7 0 0 0-1.7-.98L15 3.26h-4l-.36 2.67a7.7 7.7 0 0 0-1.7.98l-2.49-1-2 3.46 2.11 1.65a7.8 7.8 0 0 0 0 1.96l-2.11 1.65 2 3.46 2.49-1a7.7 7.7 0 0 0 1.7.98l.36 2.67h4l.35-2.67a7.7 7.7 0 0 0 1.7-.98l2.49 1 2-3.46z M13 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7z")
private val queueIcon=glyph("播放列表","M3 5h15v2H3z M3 10h11v2H3z M3 15h7v2H3z M16 10v7.2a2.8 2.8 0 1 0 2 2.7v-7.8l4-1v-2z")
private val cruiseIcon=glyph("巡航视角","M12 2c.8 0 1.5 1.5 1.5 3v3l8 6v2l-8-3v4.5l3 2.5v2l-4.5-2-4.5 2v-2l3-2.5V13l-8 3v-2l8-6V5c0-1.5.7-3 1.5-3z")
private val closeIcon=glyph("关闭","M5.6 7 7 5.6l5 5 5-5L18.4 7l-5 5 5 5-1.4 1.4-5-5-5 5L5.6 17l5-5z")
private enum class Panel(val title:String){Settings("播放设置"),Queue("播放列表")}
private val panelColors=darkColorScheme(primary=Color(0xFFACD8EF),onPrimary=night,
    surface=Color(0xFF101F2D),onSurface=frost,onSurfaceVariant=muted,
    secondaryContainer=Color(0xFF253B4C),onSecondaryContainer=frost)

@Composable internal fun TerrainPlayer(now:Playing,onBack:()->Unit,openTransfer:()->Unit,openQueue:()->Unit,
    transport:(String)->Unit,settings:@Composable ()->Unit,queue:@Composable (()->Unit)->Unit){
    var panel by rememberSaveable{mutableStateOf<Panel?>(null)}
    val state=now.data
    val loaded=state.has("song")
    val active=state.optBoolean("playing")
    fun toggle(target:Panel){
        if(panel==target){panel=null;return}
        if(target==Panel.Queue)openQueue()
        panel=target
    }
    LaunchedEffect(loaded){if(!loaded && panel==Panel.Settings)panel=null}
    BackHandler {if(panel!=null)panel=null else onBack()}
    val activity=LocalActivity.current as ComponentActivity
    val prefs=remember(activity){activity.getSharedPreferences("terrain",0)}
    var cruise by remember {mutableStateOf(prefs.getBoolean("cruise",false))}
    DisposableEffect(activity){
        val bars=WindowCompat.getInsetsController(activity.window,activity.window.decorView)
        val status=bars.isAppearanceLightStatusBars;val navigation=bars.isAppearanceLightNavigationBars
        bars.isAppearanceLightStatusBars=false;bars.isAppearanceLightNavigationBars=false
        onDispose{bars.isAppearanceLightStatusBars=status;bars.isAppearanceLightNavigationBars=navigation}
    }
    Box(Modifier.fillMaxSize().background(night)){
        SandTable(remember(state){TerrainClock.read(state)},cruise,Modifier.matchParentSize())
        Box(Modifier.fillMaxWidth().fillMaxHeight(.36f).background(Brush.verticalGradient(
            listOf(night.copy(alpha=.38f),night.copy(alpha=.16f),Color.Transparent))))
        Box(Modifier.fillMaxWidth().align(Alignment.BottomCenter).height(125.dp).background(
            Brush.verticalGradient(listOf(Color.Transparent,night.copy(alpha=.9f)))))
        Column(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing.only(
            WindowInsetsSides.Horizontal+WindowInsetsSides.Top)).imePadding()){
            Row(Modifier.fillMaxWidth().padding(horizontal=12.dp,vertical=4.dp),
                horizontalArrangement=Arrangement.SpaceBetween,verticalAlignment=Alignment.CenterVertically){
                TerrainButton(gear,"播放设置",loaded,selected=panel==Panel.Settings){toggle(Panel.Settings)}
                Row(verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(6.dp)){
                    TerrainButton(previousIcon,"上一首",state.optBoolean("previous")){transport("previous")}
                    TerrainButton(if(active)pauseIcon else playIcon,if(active)"暂停" else "播放",loaded,primary=true){
                        transport(if(active)"pause" else "resume")
                    }
                    TerrainButton(nextIcon,"下一首",state.optBoolean("next")){transport("next")}
                }
                Row {
                    TerrainButton(cruiseIcon,if(cruise)"恢复固定视角" else "开启巡航视角",true,selected=cruise){
                        cruise=!cruise;prefs.edit().putBoolean("cruise",cruise).apply()
                    }
                    TerrainButton(queueIcon,"播放列表",true,selected=panel==Panel.Queue){toggle(Panel.Queue)}
                }
            }
            BoxWithConstraints(Modifier.fillMaxSize().clipToBounds().navigationBarsPadding()){
                val side=if(maxWidth>520.dp)32.dp else 22.dp
                if(loaded){
                    Column(Modifier.align(Alignment.TopStart).padding(start=side,end=side,top=16.dp)){
                        Text(state.optString("title"),color=frost,fontWeight=FontWeight.Medium,
                            style=MaterialTheme.typography.headlineMedium,maxLines=2,overflow=TextOverflow.Ellipsis)
                        Text(state.optString("editionName"),color=muted,style=MaterialTheme.typography.bodyMedium)
                    }
                    Column(Modifier.align(Alignment.BottomCenter).fillMaxWidth().padding(horizontal=side,vertical=12.dp)){
                        val pos=state.optLong("pos");val length=state.optLong("length")
                        Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween){
                            Text(trackTime(pos),color=frost,style=MaterialTheme.typography.labelMedium)
                            Text(trackTime(length),color=muted,style=MaterialTheme.typography.labelMedium)
                        }
                        Spacer(Modifier.height(6.dp))
                        Box(Modifier.fillMaxWidth().height(2.dp).background(muted.copy(alpha=.31f))){
                            Box(Modifier.fillMaxHeight().fillMaxWidth((pos.toFloat()/length.coerceAtLeast(1)).coerceIn(0f,1f)).background(frost))
                        }
                    }
                }else if(panel==null){
                    TextButton(onClick={toggle(Panel.Queue)},modifier=Modifier.align(Alignment.TopStart).padding(side)){
                        Text(now.error.ifBlank{"选择曲目"},color=frost)
                    }
                }
                if(panel!=null){
                    Box(Modifier.fillMaxSize().background(night.copy(alpha=.34f)).clickable(
                        interactionSource=remember{MutableInteractionSource()},indication=null,onClickLabel="收起面板"){panel=null})
                }
                val maxPanelHeight=maxHeight-(if(maxHeight>360.dp)32.dp else 6.dp)
                for(target in Panel.entries){
                    androidx.compose.animation.AnimatedVisibility(visible=panel==target,
                        modifier=Modifier.align(if(target==Panel.Settings)Alignment.TopStart else Alignment.TopEnd),
                        enter=slideInVertically(tween(240)){ -it }+fadeIn(tween(140)),
                        exit=slideOutVertically(tween(170)){ -it }+fadeOut(tween(120))){
                        MaterialTheme(colorScheme=panelColors){
                            Surface(modifier=Modifier.padding(horizontal=10.dp).widthIn(max=520.dp).fillMaxWidth()
                                .heightIn(max=maxPanelHeight.coerceAtLeast(1.dp)).semantics{paneTitle=target.title},
                                shape=RoundedCornerShape(16.dp),color=panelColors.surface,
                                border=BorderStroke(1.dp,muted.copy(alpha=.22f))){
                                Column(Modifier.padding(start=16.dp,end=16.dp,bottom=10.dp)){
                                    Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically){
                                        Text(target.title,Modifier.weight(1f),style=MaterialTheme.typography.titleMedium)
                                        if(target==Panel.Queue) TextButton(onClick=openTransfer){Text("连接电脑")}
                                        IconButton(onClick={panel=null}){Icon(closeIcon,"收起"+target.title)}
                                    }
                                    HorizontalDivider(color=muted.copy(alpha=.20f))
                                    Spacer(Modifier.height(8.dp))
                                    if(target==Panel.Settings)settings() else queue{panel=null}
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable private fun TerrainButton(icon:ImageVector,label:String,enabled:Boolean,primary:Boolean=false,
    selected:Boolean=false,onClick:()->Unit){
    val tint=if(enabled)frost else muted.copy(alpha=.38f)
    val face=when{primary->Color(0xFF2D4455).copy(alpha=.76f);selected->Color(0xFF314B60);else->Color.Transparent}
    IconButton(onClick=onClick,enabled=enabled,modifier=Modifier.size(if(primary)54.dp else 48.dp)){
        Box(Modifier.size(if(primary)50.dp else 42.dp).background(face,CircleShape).then(
            if(primary)Modifier.border(1.dp,muted.copy(alpha=.42f),CircleShape) else Modifier),
            contentAlignment=Alignment.Center){Icon(icon,label,Modifier.size(if(primary)25.dp else 23.dp),tint=tint)}
    }
}
