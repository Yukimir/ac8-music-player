package local.ac8.mobile

import kotlin.random.Random

fun main(){
    val ids=listOf("first-copy","another-edition","second-copy")
    val queue=PlaybackQueue(Random(17))
    fun move(delta:Int):String?=queue.neighbor(ids,delta)?.also{queue.moved(it,delta,ids)}
    queue.start(ids.first())
    check(move(-1)==null)
    check(move(1)==ids[1] && move(1)==ids[2] && move(1)==null)
    queue.mode=PlayMode.Loop
    check(move(1)==ids.first() && move(-1)==ids.last())
    queue.mode=PlayMode.Shuffle
    val round=listOf(queue.current,move(1),move(1))
    check(round.toSet()==ids.toSet())
    check(move(-1)==round[1] && move(1)==round[2])
    check(move(1)!=round.last())
    queue.mode=PlayMode.RepeatOne
    queue.start(ids[1])
    check(move(1)==ids[2] && move(1)==null)
    check(!queue.canMove(emptyList(),1))
    queue.mode=PlayMode.Shuffle
    queue.start("only")
    check(queue.neighbor(listOf("only"),1)=="only" && !queue.canMove(listOf("only"),-1))
    println("Passed ordered boundaries, loop wrap, shuffle cycle and history, manual skip in repeat-one and one entry.")
}
