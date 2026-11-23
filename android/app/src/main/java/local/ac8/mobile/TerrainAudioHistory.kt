package local.ac8.mobile

import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference
import java.util.concurrent.atomic.AtomicReferenceArray
import kotlin.math.exp

internal data class AudioSlice(val sequence:Long,val epoch:Long,val pcm:ByteArray)

// One audio producer. Both renderers can replay the last screenful without stalling AudioTrack.
internal class TerrainAudioHistory {
    companion object { const val BLOCKS=32 }
    private val ring=AtomicReferenceArray<AudioSlice?>(BLOCKS)
    private val generation=AtomicLong()
    private val last=AtomicReference<AudioSlice?>()
    private var sequence=0L

    fun reset(){generation.incrementAndGet()}
    fun push(pcm:ByteArray):AudioSlice {
        val s=AudioSlice(++sequence,generation.get(),pcm)
        ring.set((s.sequence % BLOCKS).toInt(),s)
        last.set(s)
        return s
    }

    class Batch {
        val pcm=arrayOfNulls<ByteArray>(BLOCKS)
        var epoch=-1L
        var through=0L
        var count=0
        var newEpoch=false
        fun rewind(){epoch=-1;through=0;count=0}
    }

    fun collect(b:Batch) {
        val epoch=generation.get()
        b.newEpoch=b.epoch!=epoch;b.count=0
        if(b.newEpoch){b.epoch=epoch;b.through=0}
        val end=last.get() ?: return
        if(end.epoch!=epoch || end.sequence==b.through)return
        val start=maxOf(b.through+1,end.sequence-BLOCKS+1)
        for(n in start..end.sequence){
            val s=ring.get((n % BLOCKS).toInt())
            if(s?.epoch==epoch && s.sequence==n)b.pcm[b.count++]=s.pcm
        }
        b.through=end.sequence
    }
}

// AudioTrack may accept several blocks together. Display time advances between writes.
internal class TerrainScrollClock {
    private var cursor=Double.NaN
    fun delay(through:Long, elapsedMs:Double, reset:Boolean):Float {
        val newest=through*2.0
        if(reset || cursor.isNaN() || newest-cursor>64)cursor=newest-3
        else {
            val elapsedHops=elapsedMs*48/1024
            val correction=(newest-3-cursor)*(1-exp(-elapsedMs/180))
            cursor=minOf(newest,cursor+(elapsedHops+correction).coerceIn(0.0,1.5*elapsedHops))
        }
        return (newest-cursor).coerceIn(0.0,63.0).toFloat()
    }
}
