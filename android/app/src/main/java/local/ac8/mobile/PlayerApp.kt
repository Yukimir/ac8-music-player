package local.ac8.mobile

import android.app.Application
import android.content.SharedPreferences
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow

class PlayerApp:Application() {
    val library by lazy { Library(this) }
    val link by lazy { PhoneLink(this,library) }
    val cockpit by lazy { CockpitControls(getSharedPreferences("cockpit",MODE_PRIVATE)) }
}

internal val sceneLabels=mapOf("missile" to "导弹来袭","launch" to "发射导弹","gun" to "机炮射击",
    "railgun" to "轨道炮告警","pull_up" to "拉起告警","stall" to "失速告警","cloud" to "过云")

data class CockpitOptions(val random:Boolean,val engine:Boolean,val categories:Set<String>,val volume:Float)

class CockpitControls(private val prefs:SharedPreferences){
    private val options=MutableStateFlow(CockpitOptions(prefs.getBoolean("random",false),prefs.getBoolean("engine",false),
        prefs.getStringSet("categories",sceneLabels.keys).orEmpty().intersect(sceneLabels.keys),prefs.getFloat("volume",1f)))
    val state=options.asStateFlow()
    fun random(on:Boolean){
        options.value=options.value.copy(random=on)
        prefs.edit().putBoolean("random",on).apply()
    }
    fun engine(on:Boolean){
        options.value=options.value.copy(engine=on)
        prefs.edit().putBoolean("engine",on).apply()
    }
    fun toggle(category:String){
        val selected=options.value.categories
        val result=if(category in selected) selected-category else selected+category
        options.value=options.value.copy(categories=result)
        prefs.edit().putStringSet("categories",result).apply()
    }
    fun volume(value:Float){options.value=options.value.copy(volume=value.coerceIn(0f,2f))}
    fun saveVolume(){prefs.edit().putFloat("volume",options.value.volume).apply()}
}
