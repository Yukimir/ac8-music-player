package local.ac8.mobile

import kotlin.random.Random

enum class PlayMode(val label:String){
    Ordered("顺序播放"), Shuffle("随机播放"), Loop("列表循环"), RepeatOne("单曲循环")
}

/** Navigation belongs to the playback service, including when the screen is locked. */
internal class PlaybackQueue(private val random:Random=Random.Default){
    var current:String?=null
        private set
    var mode=PlayMode.Ordered
        set(value){
            if(value!=field){field=value;start(current)}
        }
    private val back=mutableListOf<String>()
    private val forward=mutableListOf<String>()
    private val played=mutableSetOf<String>()

    fun start(id:String?){
        current=id;back.clear();forward.clear();played.clear()
        if(id!=null)played.add(id)
    }
    fun canMove(ids:List<String>,delta:Int):Boolean{
        if(ids.isEmpty())return false
        return when(mode){
            PlayMode.Loop -> true
            PlayMode.Shuffle -> delta>0 || back.any{it in ids}
            else -> ids.indexOf(current)+delta in ids.indices
        }
    }
    fun neighbor(ids:List<String>,delta:Int):String?{
        if(!canMove(ids,delta))return null
        if(mode==PlayMode.Shuffle){
            if(delta<0)return back.lastOrNull{it in ids}
            forward.lastOrNull{it in ids}?.let{return it}
            val others=ids.filter{it!=current}
            return others.filterNot{it in played}.ifEmpty{others}.ifEmpty{ids}.random(random)
        }
        val index=ids.indexOf(current)+delta
        return ids[if(mode==PlayMode.Loop)Math.floorMod(index,ids.size) else index]
    }
    fun moved(id:String,delta:Int,ids:List<String>){
        if(mode==PlayMode.Shuffle){
            val history=if(delta<0)back else forward
            while(history.isNotEmpty() && history.last() !in ids)history.removeAt(history.lastIndex)
            if(history.lastOrNull()==id)history.removeAt(history.lastIndex)
            current?.takeIf{it!=id}?.let{(if(delta<0)forward else back).add(it)}
            if(ids.all{it in played})played.clear()
            played.add(id)
        }
        current=id
    }
}
