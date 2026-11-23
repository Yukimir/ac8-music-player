package local.ac8.mobile

fun main() {
    val ring=TerrainAudioHistory()
    val batch=TerrainAudioHistory.Batch()
    fun publish(n:Int){ring.push(byteArrayOf(n.toByte()))}
    fun read(vararg expected:Int){
        ring.collect(batch)
        check((0 until batch.count).map {batch.pcm[it]!!.first().toInt()}==expected.toList())
    }

    for(i in 1..4)publish(i)
    read(1,2,3,4)
    check(batch.newEpoch)
    // A busy render thread misses five callbacks; every audio hop is still available.
    for(i in 5..11)publish(i)
    read(5,6,7,8,9,10,11)
    check(!batch.newEpoch)
    read()
    check(!batch.newEpoch)
    // Even a stall longer than the visible terrain yields a complete replacement in one frame.
    for(i in 12..94)publish(i)
    read(*(63..94).toList().toIntArray())
    check(!batch.newEpoch)
    batch.rewind() // surface recreated
    read(*(63..94).toList().toIntArray())
    ring.reset() // an actual track change, including the gap before the first audio block
    read()
    check(batch.newEpoch)
    publish(95);read(95)
    val scroll=TerrainScrollClock()
    var last=0.0
    for(frame in 0..120){
        val published=(frame/10)*4L // bursty 4-block writes on a 60 Hz display
        val position=published*2-scroll.delay(published,1000.0/60,frame==0).toDouble()
        if(frame>0)check(position>=last-.00001 && position-last<1.18)
        last=position
    }
    println("Terrain history survived partial and full backlogs and surface recreation.")
}
