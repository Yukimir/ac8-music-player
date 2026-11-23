package local.ac8.mobile

import org.json.JSONObject
import kotlin.math.*
import kotlin.random.Random

internal data class TerrainClock(val song: Long, val clock: Long, val origin: Long, val bar: Long, val playing: Boolean) {
    companion object {
        fun read(s: JSONObject) = TerrainClock(s.optLong("song", -1), s.optLong("clock"), s.optLong("gridOrigin"),
            s.optLong("bar"), s.optBoolean("playing"))
    }
}

// The five desktop camera poses and their shuffled forward, lateral and panning moves.
internal class TerrainCruise {
    private val standard = floatArrayOf(.497f,.34f,.495f,.491f,.0293f,.497f,0f,0f)
    private val shots = arrayOf(
        floatArrayOf(.16f,.24f,.515f,.418f,.032f,.51f,0f,0f),
        floatArrayOf(.81f,.25f,.423f,.528f,.032f,.51f,0f,0f),
        floatArrayOf(.48f,.075f,.36f,.355f,.0255f,.49f,0f,0f),
        floatArrayOf(.44f,.42f,.57f,.563f,.033f,.5f,0f,0f),
        floatArrayOf(.50f,.21f,.45f,.45f,.024f,.50f,0f,1f))
    private val angles = mutableListOf<Int>()
    private val moves = Array(shots.size){mutableListOf<Int>()}
    private var shot = -1
    private var move = 0
    private var direction = 1
    private var age = 0.0
    private var duration = 0.0
    private var enabled = false
    private var lastState: TerrainClock? = null
    private var observed = 0.0
    private var lastBeat: Long? = null
    private var meanFlux = 0.0
    private var previousLevel = 0.0
    private val attacks = ArrayDeque<Double>()
    private var lastPulse = -10000.0

    private fun draw(bag: MutableList<Int>, count: Int, previous: Int): Int {
        if(bag.isEmpty()){
            bag.addAll(0 until count);bag.shuffle()
            if(bag.last()==previous){val first=bag[0];bag[0]=previous;bag[bag.lastIndex]=first}
        }
        return bag.removeAt(bag.lastIndex)
    }
    private fun cut() {
        shot=draw(angles,shots.size,shot);move=draw(moves[shot],3,-1)
        direction=if(Random.nextBoolean())1 else -1
        duration=9500.0+Random.nextDouble()*4500.0;age=0.0
    }
    fun resetClock(){
        lastState=null;lastBeat=null;attacks.clear();lastPulse=-10000.0;previousLevel=0.0;meanFlux=0.0
    }
    // A cheap onset envelope is only needed for tracks without an authored bar grid.
    private fun pulse(pcm: ByteArray?, time: Double): Boolean {
        if(pcm==null)return false
        var energy=0.0
        for(i in 0 until pcm.size-3 step 32){
            val l=(pcm[i+1].toInt() shl 8) or (pcm[i].toInt() and 255)
            val r=(pcm[i+3].toInt() shl 8) or (pcm[i+2].toInt() and 255)
            energy+=abs(l)+abs(r)
        }
        val level=ln(1+energy/(pcm.size/32.0))
        val flux=max(0.0,level-previousLevel);previousLevel=level
        val threshold=max(.085,2.3*meanFlux);meanFlux+=.12*(flux-meanFlux)
        val period=time-(attacks.lastOrNull() ?: -10000.0)
        if(flux<threshold || period<235)return false
        if(attacks.size==7)attacks.removeFirst()
        val hits=if(period in 300.0..900.0)attacks.count {
            val beats=round((time-it)/period)
            beats in 1.0..8.0 && abs(time-it-beats*period)<=min(65.0,.12*period)
        } else 0
        attacks.addLast(time)
        if(hits>=3)lastPulse=time
        return hits>=3
    }
    fun pose(out: FloatArray, cruise: Boolean, state: TerrainClock, time: Double, dt: Double, pcm: ByteArray?) {
        if(cruise!=enabled){enabled=cruise;resetClock();if(enabled)cut()}
        if(!cruise){standard.copyInto(out);return}
        val old=lastState
        if(state!==old){
            if(old==null || state.song!=old.song || state.clock<old.clock || state.origin!=old.origin ||
                state.bar!=old.bar || state.playing!=old.playing)resetClock()
            lastState=state;observed=time
        }
        age+=dt
        if(state.playing && state.bar>0){
            val sample=state.clock+(max(0.0,time-observed)*48.0).toLong()
            val beat=Math.floorDiv(sample-state.origin,state.bar)
            if(age>=duration && lastBeat!=null && beat>lastBeat!! && beat>=0)cut()
            lastBeat=beat
        }else{
            val hit=state.playing && pulse(pcm,time)
            if(age>=duration && (hit || time-lastPulse>=1800 || age>=duration+900))cut()
        }
        shots[shot].copyInto(out)
        val progress=age/duration
        val travel=(if(progress<=1)progress-.5 else .5+.4*(1-exp(-(progress-1)/.4))).toFloat()
        val offset=travel*direction
        when(move){
            0->out[6]=.16f*travel
            1->out[5]+=.16f*offset
            else->{out[0]+=.16f*offset;out[5]+=.10f*offset;out[2]+=.022f*offset;out[3]-=.022f*offset}
        }
    }
}
