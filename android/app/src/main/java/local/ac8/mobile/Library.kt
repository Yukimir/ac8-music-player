package local.ac8.mobile

import android.content.Context
import android.util.AtomicFile
import kotlinx.coroutines.flow.MutableStateFlow
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

internal fun JSONObject.objects() = keys().asSequence().map { it to getJSONObject(it) }
internal fun JSONArray.ids() = (0 until length()).map { getLong(it) }
data class Song(val id:Long,val title:String,val origin:String,val interactive:Boolean)
data class Edition(val id:String,val name:String,val infinite:Boolean)
data class LibraryEntry(val id:String,val song:Song,val edition:Edition){
    val detail get()=edition.name+" · "+if(edition.infinite)"∞ 无限循环" else "播放至结束"
}
fun catalogId(song:Long,plan:String)="$song:$plan"

class Library(context:Context) {
    val root=File(context.filesDir,"music").apply { mkdirs() }
    private val index=AtomicFile(File(root,"catalog.json"))
    private var document=if(index.baseFile.exists()) JSONObject(index.readFully().toString(Charsets.UTF_8)) else JSONObject()
    private fun group(name:String)=document.optJSONObject(name) ?: JSONObject()
    val songs=MutableStateFlow(readSongs())
    val entries=MutableStateFlow(readEntries())
    val playlist=MutableStateFlow(readPlaylist(entries.value))
    private val prefs=context.getSharedPreferences("playback",0)
    val mode=MutableStateFlow(PlayMode.entries.firstOrNull{it.name==prefs.getString("queueMode",null)} ?: PlayMode.Ordered)
    fun setMode(value:PlayMode){mode.value=value;prefs.edit().putString("queueMode",value.name).apply()}
    val revision=MutableStateFlow(0)
    private fun readSongs()=group("songs").objects().map { (id,s) ->
        Song(id.toLong(),s.getString("title"),s.optString("context"),s.getString("bank")=="互动配乐")
    }.sortedBy { it.title.lowercase() }.toList()

    @Synchronized fun plans(id:Long):List<Edition> =
        group("plans").optJSONObject(id.toString())?.objects()?.map { (key,p) ->
            val steps=p.getJSONArray("steps")
            Edition(key,p.optString("name","默认编排"),!p.isNull("loopFrom") ||
                (0 until steps.length()).any{steps.getJSONObject(it).optInt("repeat",1)==0})
        }?.sortedBy { if(it.id=="default") "" else it.name }?.toList() ?: emptyList()

    private fun readEntries()=songs.value.flatMap { song->
        val source=group("songs").getJSONObject(song.id.toString())
        val editions=plans(song.id).ifEmpty {
            val first=source.getJSONArray("order").getLong(0)
            listOf(Edition("default","单曲",source.optJSONObject("loops")?.optInt(first.toString(),1)==0))
        }
        editions.map{LibraryEntry(catalogId(song.id,it.id),song,it)}
    }

    private fun readPlaylist(all:List<LibraryEntry>):List<LibraryEntry>{
        val items=document.optJSONArray("playlist") ?: return emptyList()
        val byKey=all.associateBy{it.id}
        val seen=mutableSetOf<String>()
        return (0 until items.length()).mapNotNull { index->
            val item=items.optJSONObject(index) ?: return@mapNotNull null
            val id=item.optString("id")
            if(id.isBlank() || !seen.add(id))return@mapNotNull null
            val plan=if(item.isNull("planId"))"default" else item.getString("planId")
            byKey[catalogId(item.optLong("songId"),plan)]?.copy(id=id)
        }
    }

    fun asset(key:String):File {
        require(key.matches(Regex("[a-f0-9]{64}"))) { "音频标识无效" }
        return File(root,"$key.wav")
    }
    @Synchronized fun hasEffects()=group("cues").length()>0
    @Synchronized fun inventory():JSONObject {
        val plans=JSONObject()
        group("plans").objects().forEach { (id,p) -> plans.put(id,JSONArray(p.keys().asSequence().toList())) }
        return JSONObject().put("name",android.os.Build.MODEL).put("songs",JSONArray(songs.value.map{it.id}))
            .put("plans",plans).put("effects",hasEffects())
    }
    @Synchronized fun install(offer:JSONObject) {
        val next=JSONObject(document.toString())
        for (group in listOf("songs","segments","rules","plans","media","cues")) {
            val src=offer.getJSONObject(group)
            val dest=next.optJSONObject(group) ?: JSONObject().also{next.put(group,it)}
            for (key in src.keys()) {
                if(group=="plans") {
                    val current=dest.optJSONObject(key) ?: JSONObject().also{dest.put(key,it)}
                    src.getJSONObject(key).objects().forEach{(id,plan)->current.put(id,plan)}
                } else dest.put(key,src.get(key))
            }
        }
        offer.optJSONArray("playlist")?.let{next.put("playlist",it)}
        val output=index.startWrite()
        try { output.write(next.toString().toByteArray());index.finishWrite(output) }
        catch(e:Exception){index.failWrite(output);throw e}
        document=next;songs.value=readSongs();entries.value=readEntries();playlist.value=readPlaylist(entries.value);revision.value++
    }
}
