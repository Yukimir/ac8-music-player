#define VK_USE_PLATFORM_ANDROID_KHR
#include <vulkan/vulkan.h>
#include <android/native_window_jni.h>
#include "renderer.h"
#include <algorithm>
#include <cstring>

namespace {
const uint32_t fftCode[] =
#include "spectrum.comp.inc"
;
const uint32_t vertexCode[] =
#include "terrain.vert.inc"
;
const uint32_t fragmentCode[] =
#include "terrain.frag.inc"
;
void check(VkResult result,const char* what){
    if(result!=VK_SUCCESS)throw std::runtime_error(std::string(what)+" ("+std::to_string(result)+")");
}
constexpr auto stages=VK_SHADER_STAGE_COMPUTE_BIT|VK_SHADER_STAGE_VERTEX_BIT|VK_SHADER_STAGE_FRAGMENT_BIT;
struct Buffer {VkBuffer buffer{};VkDeviceMemory memory{};};
class VulkanRenderer final:public Renderer {
    VkInstance instance{};VkSurfaceKHR surface{};VkPhysicalDevice gpu{};
    VkDevice device{};VkQueue queue{};uint32_t family{};VkSwapchainKHR chain{};VkExtent2D extent{};
    VkRenderPass pass{};VkPipeline compute{},graphics{};VkPipelineLayout layout{};
    VkDescriptorSetLayout setLayout{};VkDescriptorPool pool{};VkDescriptorSet set{};
    VkCommandPool commands{};VkCommandBuffer cmd{};VkFence done{};VkSemaphore acquired{};
    std::vector<VkImageView> views;std::vector<VkFramebuffer> targets;std::vector<VkSemaphore> presented;
    Buffer input,history;void* mapped=nullptr;
    void buffer(Buffer& b,size_t size,VkMemoryPropertyFlags properties) {
        VkBufferCreateInfo info{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
        info.size=size;info.usage=VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
        check(vkCreateBuffer(device,&info,nullptr,&b.buffer),"buffer");
        VkMemoryRequirements req;vkGetBufferMemoryRequirements(device,b.buffer,&req);
        VkPhysicalDeviceMemoryProperties mem;vkGetPhysicalDeviceMemoryProperties(gpu,&mem);
        uint32_t type=0;
        while(type<mem.memoryTypeCount && (!(req.memoryTypeBits&(1u<<type)) ||
            (mem.memoryTypes[type].propertyFlags&properties)!=properties))++type;
        if(type==mem.memoryTypeCount)throw std::runtime_error("No compatible Vulkan buffer memory");
        VkMemoryAllocateInfo alloc{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};alloc.allocationSize=req.size;alloc.memoryTypeIndex=type;
        check(vkAllocateMemory(device,&alloc,nullptr,&b.memory),"allocate");
        check(vkBindBufferMemory(device,b.buffer,b.memory,0),"bind buffer");
    }
    void selectDevice(){
        uint32_t count=0;check(vkEnumeratePhysicalDevices(instance,&count,nullptr),"enumerate devices");
        std::vector<VkPhysicalDevice> devices(count);check(vkEnumeratePhysicalDevices(instance,&count,devices.data()),"enumerate devices");
        for(auto candidate:devices){
            uint32_t n=0;vkGetPhysicalDeviceQueueFamilyProperties(candidate,&n,nullptr);
            std::vector<VkQueueFamilyProperties> props(n);vkGetPhysicalDeviceQueueFamilyProperties(candidate,&n,props.data());
            for(uint32_t i=0;i<n;i++) {
                VkBool32 present=0;vkGetPhysicalDeviceSurfaceSupportKHR(candidate,i,surface,&present);
                constexpr auto flags=VK_QUEUE_GRAPHICS_BIT|VK_QUEUE_COMPUTE_BIT;
                if(present && (props[i].queueFlags&flags)==flags){gpu=candidate;family=i;return;}
            }
        }
        throw std::runtime_error("No graphics/compute/present queue");
    }
    void swapchain(ANativeWindow* w){
        VkSurfaceCapabilitiesKHR caps;check(vkGetPhysicalDeviceSurfaceCapabilitiesKHR(gpu,surface,&caps),"surface capabilities");
        uint32_t n=0;check(vkGetPhysicalDeviceSurfaceFormatsKHR(gpu,surface,&n,nullptr),"formats");
        if(!n)throw std::runtime_error("No presentable format");
        std::vector<VkSurfaceFormatKHR> formats(n);check(vkGetPhysicalDeviceSurfaceFormatsKHR(gpu,surface,&n,formats.data()),"formats");
        auto format=formats[0];
        for(auto f:formats)if(f.format==VK_FORMAT_R8G8B8A8_UNORM || f.format==VK_FORMAT_B8G8R8A8_UNORM){format=f;break;}
        if(format.format==VK_FORMAT_UNDEFINED)format.format=VK_FORMAT_R8G8B8A8_UNORM;
        extent=caps.currentExtent;
        if(extent.width==UINT32_MAX){
            extent.width=std::clamp(uint32_t(ANativeWindow_getWidth(w)),caps.minImageExtent.width,caps.maxImageExtent.width);
            extent.height=std::clamp(uint32_t(ANativeWindow_getHeight(w)),caps.minImageExtent.height,caps.maxImageExtent.height);
        }
        VkSwapchainCreateInfoKHR sc{VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR};
        sc.surface=surface;sc.minImageCount=std::max(3u,caps.minImageCount);
        if(caps.maxImageCount)sc.minImageCount=std::min(sc.minImageCount,caps.maxImageCount);
        sc.imageFormat=format.format;sc.imageColorSpace=format.colorSpace;sc.imageExtent=extent;sc.imageArrayLayers=1;
        sc.imageUsage=VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;sc.imageSharingMode=VK_SHARING_MODE_EXCLUSIVE;
        sc.preTransform=(caps.supportedTransforms&VK_SURFACE_TRANSFORM_IDENTITY_BIT_KHR)?VK_SURFACE_TRANSFORM_IDENTITY_BIT_KHR:caps.currentTransform;
        sc.compositeAlpha=static_cast<VkCompositeAlphaFlagBitsKHR>(caps.supportedCompositeAlpha&-caps.supportedCompositeAlpha);
        sc.presentMode=VK_PRESENT_MODE_FIFO_KHR;sc.clipped=VK_TRUE;
        check(vkCreateSwapchainKHR(device,&sc,nullptr,&chain),"swapchain");
        VkAttachmentDescription color{};color.format=format.format;color.samples=VK_SAMPLE_COUNT_1_BIT;
        color.loadOp=VK_ATTACHMENT_LOAD_OP_DONT_CARE;color.storeOp=VK_ATTACHMENT_STORE_OP_STORE;
        color.stencilLoadOp=VK_ATTACHMENT_LOAD_OP_DONT_CARE;color.stencilStoreOp=VK_ATTACHMENT_STORE_OP_DONT_CARE;
        color.initialLayout=VK_IMAGE_LAYOUT_UNDEFINED;color.finalLayout=VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;
        VkAttachmentReference ref{0,VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL};
        VkSubpassDescription sub{};sub.pipelineBindPoint=VK_PIPELINE_BIND_POINT_GRAPHICS;sub.colorAttachmentCount=1;sub.pColorAttachments=&ref;
        VkSubpassDependency dep{VK_SUBPASS_EXTERNAL,0,VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,
            VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,0,VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT,0};
        VkRenderPassCreateInfo rp{VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO};rp.attachmentCount=1;rp.pAttachments=&color;
        rp.subpassCount=1;rp.pSubpasses=&sub;rp.dependencyCount=1;rp.pDependencies=&dep;
        check(vkCreateRenderPass(device,&rp,nullptr,&pass),"render pass");
        check(vkGetSwapchainImagesKHR(device,chain,&n,nullptr),"images");std::vector<VkImage> images(n);
        check(vkGetSwapchainImagesKHR(device,chain,&n,images.data()),"images");
        views.resize(n);targets.resize(n);presented.resize(n);
        for(uint32_t i=0;i<n;i++){
            VkImageViewCreateInfo v{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};v.image=images[i];v.viewType=VK_IMAGE_VIEW_TYPE_2D;
            v.format=format.format;v.subresourceRange={VK_IMAGE_ASPECT_COLOR_BIT,0,1,0,1};
            check(vkCreateImageView(device,&v,nullptr,&views[i]),"image view");
            VkFramebufferCreateInfo f{VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO};f.renderPass=pass;f.attachmentCount=1;
            f.pAttachments=&views[i];f.width=extent.width;f.height=extent.height;f.layers=1;
            check(vkCreateFramebuffer(device,&f,nullptr,&targets[i]),"framebuffer");
            VkSemaphoreCreateInfo sem{VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};
            check(vkCreateSemaphore(device,&sem,nullptr,&presented[i]),"present semaphore");
        }
    }
    void descriptors(){
        buffer(input,sizeof(Samples),VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
        check(vkMapMemory(device,input.memory,0,sizeof(Samples),0,&mapped),"map PCM");
        buffer(history,bands*rows*2*sizeof(float),VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
        VkDescriptorSetLayoutBinding bindings[2]={{0,VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,1,VK_SHADER_STAGE_COMPUTE_BIT,nullptr},
            {1,VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,1,stages,nullptr}};
        VkDescriptorSetLayoutCreateInfo sl{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};sl.bindingCount=2;sl.pBindings=bindings;
        check(vkCreateDescriptorSetLayout(device,&sl,nullptr,&setLayout),"set layout");
        VkPushConstantRange range{stages,0,sizeof(Params)};
        VkPipelineLayoutCreateInfo pl{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};pl.setLayoutCount=1;pl.pSetLayouts=&setLayout;
        pl.pushConstantRangeCount=1;pl.pPushConstantRanges=&range;check(vkCreatePipelineLayout(device,&pl,nullptr,&layout),"pipeline layout");
        VkDescriptorPoolSize size{VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,2};
        VkDescriptorPoolCreateInfo dp{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};dp.maxSets=1;dp.poolSizeCount=1;dp.pPoolSizes=&size;
        check(vkCreateDescriptorPool(device,&dp,nullptr,&pool),"descriptor pool");
        VkDescriptorSetAllocateInfo ai{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};ai.descriptorPool=pool;ai.descriptorSetCount=1;ai.pSetLayouts=&setLayout;
        check(vkAllocateDescriptorSets(device,&ai,&set),"descriptor set");
        VkDescriptorBufferInfo infos[2]={{input.buffer,0,VK_WHOLE_SIZE},{history.buffer,0,VK_WHOLE_SIZE}};
        for(uint32_t i=0;i<2;i++){
            VkWriteDescriptorSet wr{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};wr.dstSet=set;wr.dstBinding=i;
            wr.descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;wr.descriptorCount=1;wr.pBufferInfo=&infos[i];vkUpdateDescriptorSets(device,1,&wr,0,nullptr);
        }
    }
    void pipelines(){
        std::array<VkShaderModule,3> modules{};
        try{
            const uint32_t* codes[]={fftCode,vertexCode,fragmentCode};
            const size_t sizes[]={sizeof(fftCode),sizeof(vertexCode),sizeof(fragmentCode)};
            VkShaderStageFlagBits types[]={VK_SHADER_STAGE_COMPUTE_BIT,VK_SHADER_STAGE_VERTEX_BIT,VK_SHADER_STAGE_FRAGMENT_BIT};
            VkPipelineShaderStageCreateInfo shader[3]{};
            for(int i=0;i<3;i++){
                VkShaderModuleCreateInfo mod{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};mod.codeSize=sizes[i];mod.pCode=codes[i];
                check(vkCreateShaderModule(device,&mod,nullptr,&modules[i]),"shader");
                shader[i].sType=VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;shader[i].stage=types[i];shader[i].module=modules[i];shader[i].pName="main";
            }
            VkComputePipelineCreateInfo cp{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};cp.stage=shader[0];cp.layout=layout;
            check(vkCreateComputePipelines(device,VK_NULL_HANDLE,1,&cp,nullptr,&compute),"FFT pipeline");
            VkPipelineVertexInputStateCreateInfo vertex{VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO};
            VkPipelineInputAssemblyStateCreateInfo assembly{VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO};assembly.topology=VK_PRIMITIVE_TOPOLOGY_TRIANGLE_STRIP;
            VkViewport viewport{0,0,float(extent.width),float(extent.height),0,1};VkRect2D scissor{{0,0},extent};
            VkPipelineViewportStateCreateInfo vp{VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO};
            vp.viewportCount=vp.scissorCount=1;vp.pViewports=&viewport;vp.pScissors=&scissor;
            VkPipelineRasterizationStateCreateInfo raster{VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO};raster.lineWidth=1;raster.frontFace=VK_FRONT_FACE_COUNTER_CLOCKWISE;
            VkPipelineMultisampleStateCreateInfo ms{VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO};ms.rasterizationSamples=VK_SAMPLE_COUNT_1_BIT;
            VkPipelineColorBlendAttachmentState blend{};blend.blendEnable=VK_TRUE;blend.srcColorBlendFactor=VK_BLEND_FACTOR_SRC_ALPHA;
            blend.dstColorBlendFactor=VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA;blend.srcAlphaBlendFactor=VK_BLEND_FACTOR_ONE;
            blend.dstAlphaBlendFactor=VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA;blend.colorWriteMask=15;
            VkPipelineColorBlendStateCreateInfo cb{VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO};cb.attachmentCount=1;cb.pAttachments=&blend;
            VkGraphicsPipelineCreateInfo gp{VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO};gp.stageCount=2;gp.pStages=shader+1;
            gp.pVertexInputState=&vertex;gp.pInputAssemblyState=&assembly;gp.pViewportState=&vp;gp.pRasterizationState=&raster;
            gp.pMultisampleState=&ms;gp.pColorBlendState=&cb;gp.layout=layout;gp.renderPass=pass;
            check(vkCreateGraphicsPipelines(device,VK_NULL_HANDLE,1,&gp,nullptr,&graphics),"terrain pipeline");
        }catch(...){for(auto m:modules)vkDestroyShaderModule(device,m,nullptr);throw;}
        for(auto m:modules)vkDestroyShaderModule(device,m,nullptr);
    }
public:
    void init(ANativeWindow* w){
        const char* ext[]={VK_KHR_SURFACE_EXTENSION_NAME,VK_KHR_ANDROID_SURFACE_EXTENSION_NAME};
        VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};app.pApplicationName="AC8 Terrain";app.apiVersion=VK_API_VERSION_1_0;
        VkInstanceCreateInfo info{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};info.pApplicationInfo=&app;info.enabledExtensionCount=2;info.ppEnabledExtensionNames=ext;
        check(vkCreateInstance(&info,nullptr,&instance),"Vulkan instance");
        VkAndroidSurfaceCreateInfoKHR surfaceInfo{VK_STRUCTURE_TYPE_ANDROID_SURFACE_CREATE_INFO_KHR};surfaceInfo.window=w;
        check(vkCreateAndroidSurfaceKHR(instance,&surfaceInfo,nullptr,&surface),"Android surface");selectDevice();
        float priority=1;VkDeviceQueueCreateInfo q{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};q.queueFamilyIndex=family;q.queueCount=1;q.pQueuePriorities=&priority;
        const char* swap=VK_KHR_SWAPCHAIN_EXTENSION_NAME;VkDeviceCreateInfo d{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
        d.queueCreateInfoCount=1;d.pQueueCreateInfos=&q;d.enabledExtensionCount=1;d.ppEnabledExtensionNames=&swap;
        check(vkCreateDevice(gpu,&d,nullptr,&device),"Vulkan device");vkGetDeviceQueue(device,family,0,&queue);
        swapchain(w);descriptors();pipelines();
        VkCommandPoolCreateInfo pc{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};pc.queueFamilyIndex=family;pc.flags=VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
        check(vkCreateCommandPool(device,&pc,nullptr,&commands),"command pool");
        VkCommandBufferAllocateInfo ai{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};ai.commandPool=commands;ai.commandBufferCount=1;
        check(vkAllocateCommandBuffers(device,&ai,&cmd),"command buffer");VkFenceCreateInfo fi{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};fi.flags=VK_FENCE_CREATE_SIGNALED_BIT;
        check(vkCreateFence(device,&fi,nullptr,&done),"fence");VkSemaphoreCreateInfo si{VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};
        check(vkCreateSemaphore(device,&si,nullptr,&acquired),"acquire semaphore");
        VkPhysicalDeviceProperties props;vkGetPhysicalDeviceProperties(gpu,&props);LOGI("Vulkan %s, GPU FFT, %ux%u",props.deviceName,extent.width,extent.height);
    }
    int draw(const Params& params,int hops) override{
        check(vkWaitForFences(device,1,&done,VK_TRUE,1000000000),"frame fence");
        uint32_t image=0;auto ac=vkAcquireNextImageKHR(device,chain,250000000,acquired,VK_NULL_HANDLE,&image);
        if(ac==VK_ERROR_OUT_OF_DATE_KHR || ac==VK_TIMEOUT)return 1;
        if(ac!=VK_SUBOPTIMAL_KHR)check(ac,"acquire");
        Params p=params;p.screen[0]=float(extent.width);p.screen[1]=float(extent.height);
        check(vkResetCommandBuffer(cmd,0),"reset command buffer");
        VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};begin.flags=VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
        check(vkBeginCommandBuffer(cmd,&begin),"begin frame");vkCmdPushConstants(cmd,layout,stages,0,sizeof(p),&p);
        if(hops){
            std::memcpy(mapped,samples.data(),(hops+1)*hop*sizeof(uint32_t));vkCmdBindPipeline(cmd,VK_PIPELINE_BIND_POINT_COMPUTE,compute);
            vkCmdBindDescriptorSets(cmd,VK_PIPELINE_BIND_POINT_COMPUTE,layout,0,1,&set,0,nullptr);
            vkCmdDispatch(cmd,hops,1,1);
            VkMemoryBarrier bar{VK_STRUCTURE_TYPE_MEMORY_BARRIER};bar.srcAccessMask=VK_ACCESS_SHADER_WRITE_BIT;bar.dstAccessMask=VK_ACCESS_SHADER_READ_BIT;
            vkCmdPipelineBarrier(cmd,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_VERTEX_SHADER_BIT|VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT,0,1,&bar,0,nullptr,0,nullptr);
        }
        VkRenderPassBeginInfo rp{VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO};rp.renderPass=pass;rp.framebuffer=targets[image];rp.renderArea={{0,0},extent};
        vkCmdBeginRenderPass(cmd,&rp,VK_SUBPASS_CONTENTS_INLINE);vkCmdBindPipeline(cmd,VK_PIPELINE_BIND_POINT_GRAPHICS,graphics);
        vkCmdBindDescriptorSets(cmd,VK_PIPELINE_BIND_POINT_GRAPHICS,layout,0,1,&set,0,nullptr);
        vkCmdDraw(cmd,4,p.instances(),0,0);vkCmdEndRenderPass(cmd);check(vkEndCommandBuffer(cmd),"end frame");
        VkPipelineStageFlags wait=VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT;VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};
        submit.waitSemaphoreCount=1;submit.pWaitSemaphores=&acquired;submit.pWaitDstStageMask=&wait;submit.commandBufferCount=1;submit.pCommandBuffers=&cmd;
        submit.signalSemaphoreCount=1;submit.pSignalSemaphores=&presented[image];check(vkResetFences(device,1,&done),"reset fence");
        check(vkQueueSubmit(queue,1,&submit,done),"submit");
        VkPresentInfoKHR pi{VK_STRUCTURE_TYPE_PRESENT_INFO_KHR};pi.waitSemaphoreCount=1;pi.pWaitSemaphores=&presented[image];
        pi.swapchainCount=1;pi.pSwapchains=&chain;pi.pImageIndices=&image;
        auto result=vkQueuePresentKHR(queue,&pi);
        if(result==VK_ERROR_OUT_OF_DATE_KHR)return 1;
        if(result!=VK_SUBOPTIMAL_KHR)check(result,"present");
        return 0;
    }
    ~VulkanRenderer() override{
        if(device){
            vkDeviceWaitIdle(device);vkDestroyFence(device,done,nullptr);vkDestroySemaphore(device,acquired,nullptr);
            for(auto s:presented)vkDestroySemaphore(device,s,nullptr);
            vkDestroyCommandPool(device,commands,nullptr);vkDestroyPipeline(device,graphics,nullptr);vkDestroyPipeline(device,compute,nullptr);
            vkDestroyPipelineLayout(device,layout,nullptr);vkDestroyDescriptorPool(device,pool,nullptr);vkDestroyDescriptorSetLayout(device,setLayout,nullptr);
            if(mapped)vkUnmapMemory(device,input.memory);
            for(auto b:{input,history}){vkDestroyBuffer(device,b.buffer,nullptr);vkFreeMemory(device,b.memory,nullptr);}
            for(auto f:targets)vkDestroyFramebuffer(device,f,nullptr);
            for(auto v:views)vkDestroyImageView(device,v,nullptr);
            vkDestroyRenderPass(device,pass,nullptr);vkDestroySwapchainKHR(device,chain,nullptr);vkDestroyDevice(device,nullptr);
        }
        if(surface)vkDestroySurfaceKHR(instance,surface,nullptr);
        if(instance)vkDestroyInstance(instance,nullptr);
    }
};
}
extern "C" JNIEXPORT jlong JNICALL Java_local_ac8_mobile_TerrainGpu_openVulkan(JNIEnv* env,jobject,jobject surface){
    std::unique_ptr<ANativeWindow,decltype(&ANativeWindow_release)> w(ANativeWindow_fromSurface(env,surface),ANativeWindow_release);
    if(!w)return 0;
    try {
        auto r=std::make_unique<VulkanRenderer>();r->init(w.get());
        return reinterpret_cast<jlong>(r.release());
    }catch(const std::exception& e){LOGE("Vulkan: %s",e.what());return 0;}
}
