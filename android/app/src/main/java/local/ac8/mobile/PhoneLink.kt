package local.ac8.mobile

import android.content.Context
import android.net.ConnectivityManager
import android.net.LinkAddress
import android.net.Network
import android.net.NetworkCapabilities
import android.util.Base64
import android.util.Patterns
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.URL
import java.security.MessageDigest
import java.util.UUID
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

data class Connection(val active:Boolean=false,val text:String="尚未连接",val host:String="",val progress:Float?=null)
private data class Route(val host:String,val network:Network?,val prefix:Int)
private fun prefixMatch(remote:ByteArray,local:LinkAddress):Int {
    val address=local.address.address
    if(remote.size!=address.size) return -1
    for (bit in 0 until local.prefixLength) {
        val mask=1 shl (7-bit%8)
        if(((remote[bit/8].toInt() xor address[bit/8].toInt()) and mask)!=0)return -1
    }
    return local.prefixLength
}

class PhoneLink(private val context:Context,private val library:Library) {
    val state=MutableStateFlow(Connection())
    private val scope=CoroutineScope(SupervisorJob()+Dispatchers.IO)
    private var task:Job?=null
    private val device=context.getSharedPreferences("pair",0).let {
        it.getString("id",null) ?: UUID.randomUUID().toString().also { id->it.edit().putString("id",id).apply() }
    }
    private fun routes(ips:List<String>):List<Route> {
        val cm=context.getSystemService(ConnectivityManager::class.java)
        val wifi=cm.allNetworks.firstOrNull{cm.getNetworkCapabilities(it)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)==true}
        val link=wifi?.let(cm::getLinkProperties)
        return ips.distinct().mapNotNull { value->
            if(':' in value || !Patterns.IP_ADDRESS.matcher(value).matches())return@mapNotNull null
            val addr=InetAddress.getByName(value)
            if(addr.isAnyLocalAddress || addr.isMulticastAddress || addr.isLoopbackAddress)return@mapNotNull null
            val prefix=link?.linkAddresses?.maxOfOrNull{prefixMatch(addr.address,it)} ?: -1
            Route(value,wifi,prefix)
        }.sortedByDescending{it.prefix}
    }
    fun disconnect() {task?.cancel();state.value=Connection(text="已断开连接")}
    fun connect(code:String) {
        val old=task
        task=scope.launch {
            old?.cancelAndJoin()
            state.value=Connection(true,"正在查找同一 Wi-Fi 上的电脑…")
            try {
                require(code.startsWith("ac8pair:") && code.length<8192){"这不是 AC8 播放器的配对码"}
                val pair=JSONObject(String(Base64.decode(code.removePrefix("ac8pair:"),Base64.URL_SAFE or Base64.NO_WRAP)))
                require(pair.getInt("ac8")==1)
                val token=pair.getString("token");require(token.matches(Regex("[A-Za-z0-9_-]{40,100}")))
                val port=pair.getInt("port");require(port in 1..65535)
                val ips=pair.getJSONArray("ips");require(ips.length() in 1..64)
                val candidates=routes((0 until ips.length()).map{ips.getString(it)})
                require(candidates.isNotEmpty()){"配对码里没有可用的 IPv4 地址"}
                var last="";var chosen:Route?=null
                fun open(route:Route,path:String,probe:Boolean=false):HttpURLConnection {
                    val url=URL("http://"+route.host+":"+port+path)
                    return ((route.network?.openConnection(url) ?: url.openConnection()) as HttpURLConnection).apply {
                        connectTimeout=if(probe) 1250 else 4000;readTimeout=if(probe) 2500 else 120000
                        instanceFollowRedirects=false;useCaches=false
                        if(path!="/identify"){
                            setRequestProperty("Authorization","Bearer "+token);setRequestProperty("X-AC8-Device",device)
                        }
                    }
                }
                fun json(route:Route,path:String,body:JSONObject?=null,probe:Boolean=false):JSONObject {
                    val c=open(route,path,probe)
                    try {
                        if(body!=null){c.requestMethod="POST";c.doOutput=true;c.setRequestProperty("Content-Type","application/json")
                            c.outputStream.use{it.write(body.toString().toByteArray())}}
                        require(c.responseCode==200){"电脑返回 "+c.responseCode}
                        return JSONObject(c.inputStream.bufferedReader().use{it.readText()})
                    } finally {c.disconnect()}
                }
                for(candidate in candidates){
                    ensureActive()
                    try {
                        // Private subnets can overlap. Verify the PC before sending the phone's catalog.
                        val nonce=UUID.randomUUID().toString()
                        val check=open(candidate,"/identify",true)
                        val proof=try {
                            check.setRequestProperty("X-AC8-Challenge",nonce)
                            check.inputStream.bufferedReader().use{JSONObject(it.readLine()).getString("proof")}
                        } finally {check.disconnect()}
                        val mac=Mac.getInstance("HmacSHA256")
                        mac.init(SecretKeySpec(token.toByteArray(),"HmacSHA256"))
                        require(mac.doFinal(nonce.toByteArray()).joinToString(""){"%02x".format(it)}==proof)
                        require(json(candidate,"/pair",library.inventory(),true).getInt("ac8")==1)
                        chosen=candidate;break
                    }
                    catch(e:Exception){last=e.message.orEmpty()}
                }
                val route=chosen ?: error("无法连接电脑。请确认在同一局域网，并允许播放器通过 Windows 防火墙。 "+last)
                var info="已连接，等待电脑选曲"
                while(isActive){
                    state.value=Connection(true,info,route.host)
                    val offer=json(route,"/offer")
                    if(!offer.has("id")){delay(850);continue}
                    val id=offer.getString("id");require(id.matches(Regex("[a-f0-9]{24}")))
                    fun report(text:String,flag:String?=null) {
                        val body=JSONObject().put("id",id).put("message",text)
                        if(flag!=null)body.put(flag,true)
                        json(route,"/progress",body)
                    }
                    try{
                        val needed=listOf("media","cues").flatMap { group->
                            val keys=offer.getJSONObject(group);keys.keys().asSequence().map{keys.getString(it)}.toList()
                        }.distinct().filterNot{library.asset(it).isFile}
                        for((i,key) in needed.withIndex()){
                            ensureActive()
                            val label="接收音频 "+(i+1)+" / "+needed.size
                            state.value=Connection(true,label,route.host,i.toFloat()/needed.size);report(label)
                            val file=library.asset(key)
                            val temp=File.createTempFile("receive-", ".part",library.root)
                            val c=open(route,"/asset/$id/$key")
                            try {
                                require(c.responseCode==200){"读取音源失败 "+c.responseCode}
                                val size=c.contentLengthLong
                                require(size in 44..2_147_483_647L && library.root.usableSpace>size+8_388_608){"手机存储空间不足或文件大小无效"}
                                val digest=MessageDigest.getInstance("SHA-256")
                                var count=0L
                                c.inputStream.use{input->FileOutputStream(temp).use{out->
                                    val buf=ByteArray(131072)
                                    while(true){ensureActive();val n=input.read(buf);if(n<0)break
                                        count+=n;require(count<=size);out.write(buf,0,n);digest.update(buf,0,n)}
                                }}
                                val hash=digest.digest().joinToString(""){"%02x".format(it)}
                                require(count==size && hash==c.getHeaderField("X-Content-SHA256")){"文件校验失败"}
                                check(temp.renameTo(file)){"手机无法保存文件"}
                            } finally {c.disconnect();temp.delete()}
                        }
                        ensureActive();library.install(offer)
                        json(route,"/pair",library.inventory());info="传输完成 · "+offer.getJSONObject("songs").length()+" 首曲目"
                        report(info,"done")
                    } catch(e:CancellationException){throw e}
                      catch(e:Exception){info="传输失败 · "+e.message;report(info,"failed")}
                }
            } catch(e:CancellationException){throw e}
              catch(e:Exception){state.value=Connection(text="连接已结束 · "+(e.message ?: "请重新扫码"))}
        }
    }
}
