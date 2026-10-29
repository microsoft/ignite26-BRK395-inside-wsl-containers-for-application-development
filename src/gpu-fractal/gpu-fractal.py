# Copyright (C) Microsoft Corporation. All rights reserved.
import ctypes as ct
import json
import logging
import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


WIDTH, HEIGHT = 768, 480

# The Windows NVIDIA driver JIT-compiles this PTX; no CUDA toolkit is needed in the image.
PTX = b'''
.version 7.0
.target sm_52
.address_size 64

.visible .entry mandelbrot(
    .param .u64 output,
    .param .u32 width,
    .param .u32 height,
    .param .f64 center_x,
    .param .f64 center_y,
    .param .f64 scale,
    .param .u32 max_iterations
)
{
    .reg .pred %p<6>;
    .reg .b32 %r<30>;
    .reg .b64 %rd<5>;
    .reg .f64 %d<18>;

    ld.param.u64 %rd1, [output];
    ld.param.u32 %r0, [width];
    ld.param.u32 %r1, [height];
    ld.param.u32 %r2, [max_iterations];
    ld.param.f64 %d1, [center_x];
    ld.param.f64 %d2, [center_y];
    ld.param.f64 %d3, [scale];

    mov.u32 %r3, %ctaid.x;
    mov.u32 %r4, %ntid.x;
    mov.u32 %r5, %tid.x;
    mad.lo.u32 %r6, %r3, %r4, %r5;
    mov.u32 %r7, %ctaid.y;
    mov.u32 %r8, %ntid.y;
    mov.u32 %r9, %tid.y;
    mad.lo.u32 %r10, %r7, %r8, %r9;
    setp.ge.u32 %p1, %r6, %r0;
    setp.ge.u32 %p2, %r10, %r1;
    or.pred %p3, %p1, %p2;
    @%p3 bra DONE;

    cvt.rn.f64.u32 %d4, %r6;
    cvt.rn.f64.u32 %d5, %r0;
    mul.rn.f64 %d5, %d5, 0d3FE0000000000000;
    sub.rn.f64 %d4, %d4, %d5;
    fma.rn.f64 %d6, %d4, %d3, %d1;
    cvt.rn.f64.u32 %d7, %r10;
    cvt.rn.f64.u32 %d8, %r1;
    mul.rn.f64 %d8, %d8, 0d3FE0000000000000;
    sub.rn.f64 %d7, %d7, %d8;
    fma.rn.f64 %d9, %d7, %d3, %d2;

    mov.f64 %d10, 0d0000000000000000;
    mov.f64 %d11, 0d0000000000000000;
    mov.u32 %r12, 0;
LOOP:
    mul.rn.f64 %d12, %d10, %d10;
    mul.rn.f64 %d13, %d11, %d11;
    add.rn.f64 %d14, %d12, %d13;
    setp.gt.f64 %p4, %d14, 0d4010000000000000;
    @%p4 bra ESCAPED;
    setp.ge.u32 %p5, %r12, %r2;
    @%p5 bra INTERIOR;
    mul.rn.f64 %d15, %d10, %d11;
    add.rn.f64 %d15, %d15, %d15;
    add.rn.f64 %d11, %d15, %d9;
    sub.rn.f64 %d16, %d12, %d13;
    add.rn.f64 %d10, %d16, %d6;
    add.u32 %r12, %r12, 1;
    bra LOOP;
ESCAPED:
    mul.lo.u32 %r20, %r12, 9;
    and.b32 %r20, %r20, 255;
    mul.lo.u32 %r21, %r12, 7;
    and.b32 %r21, %r21, 255;
    shl.b32 %r21, %r21, 8;
    mad.lo.u32 %r22, %r12, 5, 64;
    and.b32 %r22, %r22, 255;
    shl.b32 %r22, %r22, 16;
    or.b32 %r23, %r20, %r21;
    or.b32 %r23, %r23, %r22;
    or.b32 %r23, %r23, 0xff000000;
    bra WRITE;
INTERIOR:
    mov.u32 %r23, 0xff181008;
WRITE:
    mad.lo.u32 %r24, %r10, %r0, %r6;
    mul.wide.u32 %rd2, %r24, 4;
    add.u64 %rd3, %rd1, %rd2;
    st.global.u32 [%rd3], %r23;
DONE:
    ret;
}
'''


class CudaError(RuntimeError):
    pass


class GpuBusy(RuntimeError):
    pass


class Cuda:
    def __init__(self):
        try:
            self.lib = ct.CDLL('/usr/lib/wsl/lib/libcuda.so.1')
        except OSError as error:
            raise CudaError(
                'CUDA driver unavailable. Run with --gpus all on an NVIDIA CUDA-capable '
                'Windows machine with a WSL-compatible NVIDIA driver. There is no CPU fallback.'
            ) from error
        self.lock = threading.Lock()
        self.bind('cuGetErrorString', [ct.c_int, ct.POINTER(ct.c_char_p)])
        self.call('cuInit', [ct.c_uint], 0)
        device = ct.c_int()
        self.call('cuDeviceGet', [ct.POINTER(ct.c_int), ct.c_int], ct.byref(device), 0)
        name = ct.create_string_buffer(256)
        self.call('cuDeviceGetName', [ct.c_void_p, ct.c_int, ct.c_int], name, len(name), device)
        self.name = name.value.decode()
        total = ct.c_size_t()
        self.call('cuDeviceTotalMem_v2', [ct.POINTER(ct.c_size_t), ct.c_int], ct.byref(total), device)
        self.total_mib = total.value // (1024 * 1024)
        version = ct.c_int()
        self.call('cuDriverGetVersion', [ct.POINTER(ct.c_int)], ct.byref(version))
        self.driver_api = f'{version.value // 1000}.{(version.value % 1000) // 10}'
        self.context = ct.c_void_p()
        self.call('cuCtxCreate_v2', [ct.POINTER(ct.c_void_p), ct.c_uint, ct.c_int],
                  ct.byref(self.context), 0, device)
        self.module = ct.c_void_p()
        ptx = ct.create_string_buffer(PTX)
        self.call('cuModuleLoadData', [ct.POINTER(ct.c_void_p), ct.c_void_p],
                  ct.byref(self.module), ptx)
        self.kernel = ct.c_void_p()
        self.call('cuModuleGetFunction', [ct.POINTER(ct.c_void_p), ct.c_void_p, ct.c_char_p],
                  ct.byref(self.kernel), self.module, b'mandelbrot')
        self.output = ct.c_uint64()
        self.byte_count = WIDTH * HEIGHT * 4
        self.call('cuMemAlloc_v2', [ct.POINTER(ct.c_uint64), ct.c_size_t],
                  ct.byref(self.output), self.byte_count)
        self.host = (ct.c_ubyte * self.byte_count)()
        self.start, self.end = ct.c_void_p(), ct.c_void_p()
        for event in (self.start, self.end):
            self.call('cuEventCreate', [ct.POINTER(ct.c_void_p), ct.c_uint], ct.byref(event), 0)
        self.bind('cuLaunchKernel', [ct.c_void_p] + [ct.c_uint] * 7 +
                  [ct.c_void_p, ct.POINTER(ct.c_void_p), ct.POINTER(ct.c_void_p)])
        self.bind('cuCtxSetCurrent', [ct.c_void_p])
        self.bind('cuEventRecord', [ct.c_void_p, ct.c_void_p])
        self.bind('cuEventSynchronize', [ct.c_void_p])
        self.bind('cuEventElapsedTime', [ct.POINTER(ct.c_float), ct.c_void_p, ct.c_void_p])
        self.bind('cuMemcpyDtoH_v2', [ct.c_void_p, ct.c_uint64, ct.c_size_t])
        self.bind('cuCtxDestroy_v2', [ct.c_void_p])

    def bind(self, name, types):
        function = getattr(self.lib, name)
        function.argtypes, function.restype = types, ct.c_int
        return function

    def check(self, code, name):
        if code:
            message = ct.c_char_p()
            self.lib.cuGetErrorString(code, ct.byref(message))
            detail = message.value.decode() if message.value else f'error {code}'
            raise CudaError(f'{name}: {detail}')

    def call(self, name, types, *args):
        self.check(self.bind(name, types)(*args), name)

    def invoke(self, name, *args):
        self.check(getattr(self.lib, name)(*args), name)

    def render(self, x, y, zoom, iterations):
        if not self.lock.acquire(blocking=False):
            raise GpuBusy('Another GPU frame is in progress. Try again.')
        try:
            started = time.perf_counter()
            self.invoke('cuCtxSetCurrent', self.context)
            values = (
                self.output, ct.c_uint(WIDTH), ct.c_uint(HEIGHT),
                ct.c_double(x), ct.c_double(y), ct.c_double(3.2 / WIDTH / zoom),
                ct.c_uint(iterations),
            )
            pointers = (ct.c_void_p * len(values))(
                *(ct.cast(ct.byref(value), ct.c_void_p) for value in values)
            )
            self.invoke('cuEventRecord', self.start, None)
            self.invoke('cuLaunchKernel', self.kernel, (WIDTH + 15) // 16, (HEIGHT + 15) // 16,
                        1, 16, 16, 1, 0, None, pointers, None)
            self.invoke('cuEventRecord', self.end, None)
            self.invoke('cuEventSynchronize', self.end)
            elapsed = ct.c_float()
            self.invoke('cuEventElapsedTime', ct.byref(elapsed), self.start, self.end)
            self.invoke('cuMemcpyDtoH_v2', self.host, self.output, self.byte_count)
            pixels = bytes(self.host)
            return pixels, elapsed.value, (time.perf_counter() - started) * 1000
        finally:
            self.lock.release()

    def close(self):
        self.invoke('cuCtxSetCurrent', self.context)
        self.invoke('cuCtxDestroy_v2', self.context)


PAGE = r'''<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CUDA Canvas / WSLC</title>
<style>
:root{color-scheme:dark;font-family:Consolas,monospace;background:#080b16;color:#d7e3ff}
body{max-width:1120px;margin:3vh auto;padding:24px}
h1{font-size:42px;margin:12px 0;color:#f3f5ff;letter-spacing:-2px}
.label{color:#8e9dbd;font-size:12px;letter-spacing:2px;text-transform:uppercase}
.green{color:#8bffbd}.muted{color:#9baac5}
.route{margin:24px 0;padding:16px;border:1px solid #293659;background:#10172b;line-height:1.9}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:20px 0}
.card{padding:18px;border:1px solid #293659;background:#10172b}
.value{font-size:26px;margin-top:8px;color:#8bffbd}
canvas{display:block;width:100%;height:auto;background:#080b16;cursor:crosshair;border:1px solid #293659}
.controls{display:flex;flex-wrap:wrap;gap:12px;margin:18px 0;align-items:center}
button,select{font:inherit;padding:12px;background:#1a2945;color:#e5edff;border:1px solid #40567c;cursor:pointer}
button.primary{background:#8bffbd;color:#08140e;border:0;font-weight:bold}
button:disabled,select:disabled{opacity:.5;cursor:default}
pre{white-space:pre-wrap;line-height:1.8;background:#10172b;padding:18px;border-left:3px solid #8bffbd}
#status{min-height:24px}.error{color:#ffac98}
footer{color:#9baac5;font-size:12px;line-height:1.7;margin-top:18px}
@media(max-width:650px){.cards{grid-template-columns:1fr}h1{font-size:32px}}
</style>
<div class="label green">WSLC / NVIDIA CUDA / real GPU pixels</div>
<h1>CUDA Canvas<span class="green">.</span></h1>
<div class="muted">Click a fractal. Cross a port. Launch hundreds of thousands of GPU threads.</div>
<div class="route" id="route">Connecting to Linux...</div>
<div class="cards">
 <div class="card"><div class="label">CUDA kernel / GPU time</div><div class="value" id="gpu">-- ms</div></div>
 <div class="card"><div class="label">Pixels computed on GPU</div><div class="value">368,640 / frame</div></div>
 <div class="card"><div class="label">Magnification</div><div class="value" id="zoom">1x</div></div>
</div>
<canvas id="canvas" width="768" height="480" aria-label="Mandelbrot set computed by the container's CUDA kernel"></canvas>
<div class="controls">
 <button class="primary" id="dive">DIVE INTO THE SPIRAL / 8x</button>
 <button id="animate">ANIMATE / 20 GPU FRAMES</button>
 <button id="reset">RESET</button>
 <select id="iterations" aria-label="Maximum fractal iterations">
  <option value="256">256 iterations</option><option value="512" selected>512 iterations</option>
  <option value="1024">1024 iterations</option>
 </select>
 <button id="save">SAVE PNG</button>
</div>
<div id="status" class="muted">Loading the CUDA renderer...</div>
<pre id="receipt">Waiting for the Linux GPU...</pre>
<footer>Every pixel is computed by a CUDA kernel inside Linux; the browser only displays the returned RGBA bytes.
No CPU fallback. Animation stops after 20 frames. Click the image to recenter and zoom 2x.</footer>
<script>
const $=id=>document.getElementById(id);
let x=-0.5,y=0,zoom=1,busy=false;
const context=$('canvas').getContext('2d');
function controls(disabled){
 for(const id of ['dive','animate','reset','iterations','save'])$(id).disabled=disabled;
}
async function frame(){
 const started=performance.now();
 const query=new URLSearchParams({x,y,zoom,iterations:$('iterations').value});
 const response=await fetch('/frame?'+query,{cache:'no-store',headers:{'X-GPU-Demo':'1'}});
 if(!response.ok){const error=await response.json();throw Error(error.error||'HTTP '+response.status);}
 const pixels=new Uint8ClampedArray(await response.arrayBuffer());
 if(pixels.length!==768*480*4)throw Error('Incomplete GPU frame');
 context.putImageData(new ImageData(pixels,768,480),0,0);
 $('gpu').textContent=Number(response.headers.get('X-GPU-Ms')).toFixed(2)+' ms';
 $('zoom').textContent=zoom.toLocaleString(undefined,{maximumFractionDigits:1})+'x';
 $('status').className='muted';
 $('status').textContent='GPU + transfer: '+Number(response.headers.get('X-Frame-Ms')).toFixed(2)+
  ' ms | browser round trip: '+(performance.now()-started).toFixed(1)+' ms | click the image to explore';
}
async function render(action){
 if(busy)return;
 busy=true;controls(true);
 try{await action();}
 catch(error){$('status').className='error';$('status').textContent='GPU render failed: '+error.message;}
 finally{busy=false;controls(false);}
}
$('dive').onclick=()=>render(async()=>{
 x=-0.743643887037151;y=0.131825904205330;zoom=Math.min(zoom*8,1e10);await frame();
});
$('animate').onclick=()=>render(async()=>{
 x=-0.743643887037151;y=0.131825904205330;
 if(zoom>=1e8)zoom=1;
 for(let i=0;i<20;i++){zoom=Math.min(zoom*1.35,1e10);await frame();
  await new Promise(resolve=>setTimeout(resolve,80));}
});
$('reset').onclick=()=>render(async()=>{x=-0.5;y=0;zoom=1;await frame();});
$('iterations').onchange=()=>render(frame);
$('canvas').onclick=event=>render(async()=>{
 const bounds=$('canvas').getBoundingClientRect();
 x+=((event.clientX-bounds.left)/bounds.width*768-384)*3.2/768/zoom;
 y+=((event.clientY-bounds.top)/bounds.height*480-240)*3.2/768/zoom;
 zoom=Math.min(zoom*2,1e10);await frame();
});
$('save').onclick=()=>{
 const link=document.createElement('a');link.download='cuda-canvas.png';
 link.href=$('canvas').toDataURL('image/png');link.click();
};
async function start(){
 controls(true);
 try{
  const response=await fetch('/api',{cache:'no-store'});
  if(!response.ok)throw Error('HTTP '+response.status);
  const d=await response.json();
  $('route').textContent='Windows '+location.host+' -> Linux :'+d.port+' -> CUDA -> '+d.gpu;
  $('receipt').textContent='GPU             '+d.gpu+'\n'+
   'GPU memory      '+d.memory_mib+' MiB\n'+
   'CUDA driver API '+d.driver_api+'\n'+
   'Linux device    '+d.device+'\n'+
   'Linux kernel    '+d.kernel+'\n'+
   'Linux hostname  '+d.hostname+'\n'+
   'Linux socket    '+d.local+'\n'+
   'renderer        CUDA driver API / FP64 Mandelbrot / no CPU fallback';
  await render(frame);
 }catch(error){$('status').className='error';$('status').textContent='Startup failed: '+error.message;}
}
start();
</script>
</html>'''


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, data, content_type='application/json', headers=None):
        if isinstance(data, str):
            data = data.encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        for name, value in (headers or {}).items():
            self.send_header(name, str(value))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == '/':
            return self.reply(200, PAGE, 'text/html; charset=utf-8')
        if url.path == '/api':
            return self.reply(200, json.dumps(dict(
                gpu=gpu.name, memory_mib=gpu.total_mib, driver_api=gpu.driver_api,
                device='/dev/dxg' if os.path.exists('/dev/dxg') else 'not present',
                kernel=os.uname().release, hostname=os.uname().nodename,
                port=self.server.server_port, width=WIDTH, height=HEIGHT,
                local=f'{self.connection.getsockname()[0]}:{self.connection.getsockname()[1]}',
            )))
        if url.path != '/frame':
            return self.reply(404, '{"error":"not found"}')
        if self.headers.get('X-GPU-Demo') != '1':
            return self.reply(403, '{"error":"X-GPU-Demo: 1 header required"}')
        try:
            params = parse_qs(url.query, keep_blank_values=True, strict_parsing=True)
            if params.keys() - {'x', 'y', 'zoom', 'iterations'} or any(len(v) != 1 for v in params.values()):
                raise ValueError('Unknown or repeated frame parameter')
            x = float(params.get('x', ['-0.5'])[0])
            y = float(params.get('y', ['0'])[0])
            zoom = float(params.get('zoom', ['1'])[0])
            iterations = int(params.get('iterations', ['512'])[0])
            if not (math.isfinite(x) and math.isfinite(y) and abs(x) <= 4 and abs(y) <= 4):
                raise ValueError('Coordinates must be finite and between -4 and 4')
            if not math.isfinite(zoom) or not 1 <= zoom <= 1e10 or iterations not in (256, 512, 1024):
                raise ValueError('Zoom must be 1..1e10; iterations must be 256, 512, or 1024')
        except ValueError as error:
            return self.reply(400, json.dumps({'error': str(error)}))
        try:
            pixels, gpu_ms, frame_ms = gpu.render(x, y, zoom, iterations)
            logging.info('CUDA frame: zoom=%.2f iterations=%d kernel=%.3fms total=%.3fms',
                         zoom, iterations, gpu_ms, frame_ms)
            self.reply(200, pixels, 'application/octet-stream',
                       {'X-GPU-Ms': f'{gpu_ms:.3f}', 'X-Frame-Ms': f'{frame_ms:.3f}'})
        except GpuBusy as error:
            self.reply(429, json.dumps({'error': str(error)}))
        except CudaError as error:
            logging.exception('CUDA render failed')
            self.reply(500, json.dumps({'error': str(error)}))

    def log_message(self, fmt, *args):
        if urlsplit(self.path).path != '/api':
            super().log_message(fmt, *args)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    try:
        gpu = Cuda()
    except CudaError as error:
        raise SystemExit(f'GPU initialization failed: {error}') from error
    logging.info('CUDA ready: %s (%d MiB), Linux device /dev/dxg=%s',
                 gpu.name, gpu.total_mib, os.path.exists('/dev/dxg'))
    try:
        ThreadingHTTPServer(('0.0.0.0', 8000), Handler).serve_forever()
    finally:
        gpu.close()
