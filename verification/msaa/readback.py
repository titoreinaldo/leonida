"""Vulkan 1.2 readback of prepare.py's translated shaders on one explicitly named physical adapter."""
import argparse
from contextlib import ExitStack
from pathlib import Path
import struct
import vulkan as v


def verify(work, device_name, clean, validate):
    def own(obj, destroy, parent):
        clean.callback(destroy, parent, obj, None)
        return obj

    inst = v.vkCreateInstance(v.VkInstanceCreateInfo(pApplicationInfo=v.VkApplicationInfo(
        pApplicationName="Leonida synthetic MSAA verification", apiVersion=v.VK_MAKE_VERSION(1,2,0)),
        ppEnabledLayerNames=["VK_LAYER_KHRONOS_validation"] if validate else None,
        ppEnabledExtensionNames=[v.VK_EXT_DEBUG_UTILS_EXTENSION_NAME] if validate else None), None)
    clean.callback(v.vkDestroyInstance, inst, None)
    validation_errors = []
    if validate:
        def capture(severity, msg_type, info, user_data):
            message = v.ffi.string(info.pMessage).decode("utf-8","replace")
            validation_errors.append(message)
            print("validation:",message,flush=True)
            return v.VK_FALSE
        args = v.VkDebugUtilsMessengerCreateInfoEXT(messageSeverity=v.VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT,
              messageType=7,pfnUserCallback=capture)
        messenger = v.vkGetInstanceProcAddr(inst,"vkCreateDebugUtilsMessengerEXT")(inst,args,None)
        clean.callback(v.vkGetInstanceProcAddr(inst,"vkDestroyDebugUtilsMessengerEXT"),inst,messenger,None)
    gpu, = [g for g in v.vkEnumeratePhysicalDevices(inst) if v.vkGetPhysicalDeviceProperties(g).deviceName == device_name]
    props = v.vkGetPhysicalDeviceProperties(gpu)
    print(f"selected {props.deviceName}, vendor {props.vendorID:04x}, device {props.deviceID:04x}, driver 0x{props.driverVersion:x}")
    families = v.vkGetPhysicalDeviceQueueFamilyProperties(gpu)
    flags = v.VK_QUEUE_GRAPHICS_BIT | v.VK_QUEUE_COMPUTE_BIT
    qi = next(i for i,f in enumerate(families) if f.queueFlags & flags == flags)
    mem = v.vkGetPhysicalDeviceMemoryProperties(gpu)
    features12 = v.VkPhysicalDeviceVulkan12Features(bufferDeviceAddress=True, runtimeDescriptorArray=True)
    features = v.VkPhysicalDeviceFeatures(shaderSampledImageArrayDynamicIndexing=True, shaderInt64=True)
    queue_info = v.VkDeviceQueueCreateInfo(queueFamilyIndex=qi, pQueuePriorities=[1.0])
    dev = v.vkCreateDevice(gpu, v.VkDeviceCreateInfo(pNext=features12, pEnabledFeatures=features,
                                                      pQueueCreateInfos=[queue_info]), None)
    clean.callback(v.vkDestroyDevice, dev, None)
    queue = v.vkGetDeviceQueue(dev, qi, 0)

    def make(kind, **kwargs):
        ctor = getattr(v, "Vk" + kind + "CreateInfo")
        obj = getattr(v, "vkCreate" + kind)(dev, ctor(**kwargs), None)
        return own(obj, getattr(v, "vkDestroy" + kind), dev)

    def allocate(req, flags, address=False):
        idx = next(i for i in range(mem.memoryTypeCount)
                   if req.memoryTypeBits & (1 << i) and mem.memoryTypes[i].propertyFlags & flags == flags)
        af = v.VkMemoryAllocateFlagsInfo(flags=0x2) if address else None  # DEVICE_ADDRESS_BIT
        return own(v.vkAllocateMemory(dev, v.VkMemoryAllocateInfo(allocationSize=req.size, memoryTypeIndex=idx,
                                                                   pNext=af),None), v.vkFreeMemory, dev)

    def buffer(size, usage, address=False):
        buf = v.vkCreateBuffer(dev, v.VkBufferCreateInfo(size=size,usage=usage), None)
        block = allocate(v.vkGetBufferMemoryRequirements(dev,buf),
                         v.VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | v.VK_MEMORY_PROPERTY_HOST_COHERENT_BIT, address)
        own(buf,v.vkDestroyBuffer,dev)
        v.vkBindBufferMemory(dev,buf,block,0)
        mapping = v.vkMapMemory(dev,block,0,size,0)
        clean.callback(v.vkUnmapMemory,dev,block)
        return buf, mapping

    def shader(path):
        code = path.read_bytes()
        return make("ShaderModule",codeSize=len(code),pCode=code)

    def stage(module, which):
        return v.VkPipelineShaderStageCreateInfo(stage=which,module=module,pName="main")

    pool = make("CommandPool",queueFamilyIndex=qi,flags=v.VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT)
    cmd = v.vkAllocateCommandBuffers(dev,v.VkCommandBufferAllocateInfo(commandPool=pool,
        level=v.VK_COMMAND_BUFFER_LEVEL_PRIMARY,commandBufferCount=1))[0]

    def begin():
        v.vkResetCommandBuffer(cmd,0)
        v.vkBeginCommandBuffer(cmd,v.VkCommandBufferBeginInfo())

    def submit():
        v.vkEndCommandBuffer(cmd)
        v.vkQueueSubmit(queue,1,[v.VkSubmitInfo(pCommandBuffers=[cmd])],v.VK_NULL_HANDLE)
        v.vkQueueWaitIdle(queue)

    U, F, S = v.VK_FORMAT_R32G32B32A32_UINT,v.VK_FORMAT_R32G32B32A32_SFLOAT,v.VK_FORMAT_R32G32B32A32_SINT
    color = v.VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL
    sampled = v.VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL
    samples = v.VK_SAMPLE_COUNT_4_BIT
    color_stage, compute = v.VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,v.VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT
    extent = v.VkExtent2D(width=2,height=2)
    rect = v.VkRect2D(offset=v.VkOffset2D(x=0,y=0),extent=extent)
    subpass = v.VkSubpassDescription(pipelineBindPoint=v.VK_PIPELINE_BIND_POINT_GRAPHICS,
                                     pColorAttachments=[v.VkAttachmentReference(attachment=0,layout=color)])
    rp = make("RenderPass",pAttachments=[v.VkAttachmentDescription(format=U,samples=samples,
        loadOp=v.VK_ATTACHMENT_LOAD_OP_DONT_CARE,storeOp=v.VK_ATTACHMENT_STORE_OP_STORE,
        stencilLoadOp=v.VK_ATTACHMENT_LOAD_OP_DONT_CARE,stencilStoreOp=v.VK_ATTACHMENT_STORE_OP_DONT_CARE,
        initialLayout=v.VK_IMAGE_LAYOUT_UNDEFINED,finalLayout=sampled)],
        pSubpasses=[subpass],
        pDependencies=[v.VkSubpassDependency(srcSubpass=0,dstSubpass=v.VK_SUBPASS_EXTERNAL,srcStageMask=color_stage,
            dstStageMask=compute,srcAccessMask=v.VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT,dstAccessMask=v.VK_ACCESS_SHADER_READ_BIT)])
    gfx_layout = make("PipelineLayout")
    stages = [stage(shader(work/"seed.vert.spv"),v.VK_SHADER_STAGE_VERTEX_BIT),
              stage(shader(work/"seed.frag.spv"),v.VK_SHADER_STAGE_FRAGMENT_BIT)]
    gp = v.VkGraphicsPipelineCreateInfo(layout=gfx_layout,renderPass=rp,subpass=0,pStages=stages,
        pVertexInputState=v.VkPipelineVertexInputStateCreateInfo(),
        pInputAssemblyState=v.VkPipelineInputAssemblyStateCreateInfo(topology=v.VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST),
        pViewportState=v.VkPipelineViewportStateCreateInfo(pViewports=[v.VkViewport(x=0,y=0,width=2,height=2,minDepth=0,maxDepth=1)],pScissors=[rect]),
        pRasterizationState=v.VkPipelineRasterizationStateCreateInfo(polygonMode=v.VK_POLYGON_MODE_FILL,
            cullMode=v.VK_CULL_MODE_NONE,frontFace=v.VK_FRONT_FACE_COUNTER_CLOCKWISE,lineWidth=1),
        pMultisampleState=v.VkPipelineMultisampleStateCreateInfo(rasterizationSamples=samples),
        pColorBlendState=v.VkPipelineColorBlendStateCreateInfo(pAttachments=[v.VkPipelineColorBlendAttachmentState(colorWriteMask=15)]))
    pipeline = own(v.vkCreateGraphicsPipelines(dev,v.VK_NULL_HANDLE,1,[gp],None)[0],v.vkDestroyPipeline,dev)
    begin()
    v.vkCmdBindPipeline(cmd,v.VK_PIPELINE_BIND_POINT_GRAPHICS,pipeline)
    views = {}
    for cls, fmt in enumerate((F,U,S)):
        img = v.vkCreateImage(dev,v.VkImageCreateInfo(flags=v.VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT,
            imageType=v.VK_IMAGE_TYPE_2D,format=U,extent=v.VkExtent3D(width=2,height=2,depth=1),mipLevels=1,
            arrayLayers=2,samples=samples,tiling=v.VK_IMAGE_TILING_OPTIMAL,
            usage=v.VK_IMAGE_USAGE_SAMPLED_BIT|v.VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT),None)
        storage = allocate(v.vkGetImageMemoryRequirements(dev,img),v.VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)
        own(img,v.vkDestroyImage,dev)
        v.vkBindImageMemory(dev,img,storage,0)

        def view(ty,fmt,base,layers):
            return make("ImageView",image=img,viewType=ty,format=fmt,
                subresourceRange=v.VkImageSubresourceRange(aspectMask=v.VK_IMAGE_ASPECT_COLOR_BIT,
                    baseMipLevel=0,levelCount=1,baseArrayLayer=base,layerCount=layers))

        for layer in (0,1):
            attachment = view(v.VK_IMAGE_VIEW_TYPE_2D,U,layer,1)
            fb = make("Framebuffer",renderPass=rp,pAttachments=[attachment],width=2,height=2,layers=1)
            v.vkCmdBeginRenderPass(cmd,v.VkRenderPassBeginInfo(renderPass=rp,framebuffer=fb,renderArea=rect),
                                    v.VK_SUBPASS_CONTENTS_INLINE)
            v.vkCmdDraw(cmd,3,4,0,8*cls+4*layer)
            v.vkCmdEndRenderPass(cmd)
        views[20+2*cls] = view(v.VK_IMAGE_VIEW_TYPE_2D,fmt,0,1)
        views[21+2*cls] = view(v.VK_IMAGE_VIEW_TYPE_2D_ARRAY,fmt,0,2)
    submit()

    sampled_ty, table_ty = v.VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,v.VK_DESCRIPTOR_TYPE_STORAGE_BUFFER
    cs_stage = v.VK_SHADER_STAGE_COMPUTE_BIT
    bindings = [v.VkDescriptorSetLayoutBinding(binding=b,descriptorType=sampled_ty,descriptorCount=2,stageFlags=cs_stage)
                for b in sorted(views)]
    bindings.append(v.VkDescriptorSetLayoutBinding(binding=2,descriptorType=table_ty,descriptorCount=1,stageFlags=cs_stage))
    dsl = make("DescriptorSetLayout",pBindings=bindings)
    dp = make("DescriptorPool",maxSets=1,pPoolSizes=[v.VkDescriptorPoolSize(type=sampled_ty,descriptorCount=12),
                                                         v.VkDescriptorPoolSize(type=table_ty,descriptorCount=1)])
    ds = v.vkAllocateDescriptorSets(dev,v.VkDescriptorSetAllocateInfo(descriptorPool=dp,pSetLayouts=[dsl]))[0]
    table, table_bytes = buffer(40,v.VK_BUFFER_USAGE_STORAGE_BUFFER_BIT)
    target, target_bytes = buffer(512,0x20000|v.VK_BUFFER_USAGE_STORAGE_BUFFER_BIT,True)  # SHADER_DEVICE_ADDRESS_BIT
    addr_info = v.VkBufferDeviceAddressInfo(buffer=target)
    addr = v.lib.vkGetBufferDeviceAddress(dev,v.ffi.addressof(addr_info))
    assert addr
    writes = [v.VkWriteDescriptorSet(dstSet=ds,dstBinding=b,descriptorType=sampled_ty,
              pImageInfo=[v.VkDescriptorImageInfo(imageView=view,imageLayout=sampled)]*2) for b,view in views.items()]
    writes.append(v.VkWriteDescriptorSet(dstSet=ds,dstBinding=2,descriptorType=table_ty,
                   pBufferInfo=[v.VkDescriptorBufferInfo(buffer=table,offset=0,range=40)]))
    v.vkUpdateDescriptorSets(dev,len(writes),writes,0,None)
    layout = make("PipelineLayout",pSetLayouts=[dsl],pPushConstantRanges=[
                  v.VkPushConstantRange(stageFlags=cs_stage,offset=0,size=144)])
    for path in sorted((work/"spirv").glob("cs_ms*.spv")):
        array = "array" in path.stem
        control = path.stem.endswith("_zero")
        compute_stage = stage(shader(path),cs_stage)
        ci = v.VkComputePipelineCreateInfo(layout=layout,stage=compute_stage)
        cp = own(v.vkCreateComputePipelines(dev,v.VK_NULL_HANDLE,1,[ci],None)[0],v.vkDestroyPipeline,dev)
        for cls,fmt in enumerate((77,75,76)):  # GFX10 RGBA32 float, uint, sint
            tsharp = [0x1000,fmt<<20,0,0,0,0,0,0]
            table_bytes[:] = struct.pack("<10I",0,*tsharp,1)
            target_bytes[:] = b"\xcd"*512
            pc = [0,0,0xffffffff,0x31016fac] + [0]*4 + tsharp + [0]*16 + [addr&0xffffffff,addr>>32,0,0]
            data = v.ffi.new("unsigned char[]",struct.pack("<36I",*pc))
            begin()
            v.vkCmdBindPipeline(cmd,v.VK_PIPELINE_BIND_POINT_COMPUTE,cp)
            v.vkCmdBindDescriptorSets(cmd,v.VK_PIPELINE_BIND_POINT_COMPUTE,layout,0,1,[ds],0,None)
            v.vkCmdPushConstants(cmd,layout,cs_stage,0,144,data)
            v.vkCmdDispatch(cmd,1,1,1)
            barrier = v.VkMemoryBarrier(srcAccessMask=v.VK_ACCESS_SHADER_WRITE_BIT,dstAccessMask=v.VK_ACCESS_HOST_READ_BIT)
            v.vkCmdPipelineBarrier(cmd,compute,v.VK_PIPELINE_STAGE_HOST_BIT,0,1,[barrier],0,None,0,None)
            submit()
            words = struct.unpack("<128I",target_bytes[:])
            bad = []
            for lane in range(32):
                x,y,sample = lane&1,(lane>>1)&1,(lane>>2)&3
                layer = (lane>>4)&1 if array else 0
                base = 0xbf800000 + (8*cls+4*layer+sample)*4096 + y*256 + x*16
                want = tuple(base+c for c in (1,2,3,4))
                if "_d16" in path.stem:
                    if cls == 0:
                        # The seed is in [-2,-1]. GFX10's RGBA32_FLOAT D16 conversion truncates its mantissa.
                        halves = [0xbc00 | ((w >> 13) & 0x3ff) for w in want]
                    else:
                        halves = [0xffff if cls == 1 else 0x8000]*4
                    want = (halves[0] | halves[1]<<16, 0xa55a0000 | halves[3], 23, 42)
                got = words[lane*4:lane*4+4]
                if got != want:
                    bad.append(lane)
                    if len(bad)==1:
                        first = (lane,[hex(w) for w in got],[hex(w) for w in want])
            expected_bad = [i for i in range(32) if (i>>2)&3] if control else []
            if bad != expected_bad:
                raise AssertionError((path.stem,cls,len(bad),first if bad else None))
            print(f"{path.stem} {('float','uint','sint')[cls]}: {32-len(bad)}/32 matching four-word texels"
                  + (" (expected negative control)" if control else ""),flush=True)
    if validate:
        print(f"validation errors: {len(validation_errors)}")
        assert not validation_errors


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("work",type=Path)
    ap.add_argument("device")
    ap.add_argument("--validation",action="store_true")
    args = ap.parse_args()
    with ExitStack() as clean:
        verify(args.work,args.device,clean,args.validation)
