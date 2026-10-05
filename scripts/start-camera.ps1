param(
    [string]$CameraName = "USB Camera",
    [string]$StreamName = "usb",
    [int]$Width = 854,
    [int]$Height = 480,
    [int]$Framerate = 20,
    [int]$BitrateKbps = 1200
)

$ffmpeg = "C:\ffmpeg\bin\ffmpeg.exe"

if (-not (Test-Path $ffmpeg)) {
    throw "FFmpeg introuvable: $ffmpeg"
}

$bitrate = "${BitrateKbps}k"
$maxrate = "${BitrateKbps}k"
$buffer = "$($BitrateKbps * 2)k"
$input = "video=$CameraName"
$output = "rtsp://127.0.0.1:8556/$StreamName"

& $ffmpeg `
    -hide_banner `
    -f dshow `
    -video_size "1280x720" `
    -framerate "30" `
    -i $input `
    -vf "scale=${Width}:${Height}:flags=fast_bilinear" `
    -r $Framerate `
    -pix_fmt yuv420p `
    -c:v libx264 `
    -preset veryfast `
    -tune zerolatency `
    -profile:v main `
    -b:v $bitrate `
    -maxrate $maxrate `
    -bufsize $buffer `
    -g ($Framerate * 2) `
    -f rtsp `
    -rtsp_transport tcp `
    $output

exit $LASTEXITCODE
