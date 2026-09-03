param(
    [Parameter(Mandatory = $true)][string]$OutputPptx,
    [Parameter(Mandatory = $true)][string]$AlphaPng
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$output = [IO.Path]::GetFullPath($OutputPptx)
$alpha = [IO.Path]::GetFullPath($AlphaPng)
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($output)) | Out-Null

$ppt = $null
$deck = $null
try {
    $ppt = New-Object -ComObject PowerPoint.Application
    $ppt.Visible = -1
    $deck = $ppt.Presentations.Add()
    $deck.PageSetup.SlideWidth = 960
    $deck.PageSetup.SlideHeight = 540

    # Slide 1: topology and paint torture.
    $s1 = $deck.Slides.Add(1, 12)
    $title = $s1.Shapes.AddTextbox(1, 36, 20, 888, 32)
    $title.TextFrame.TextRange.Text = "LINE / DASH / SHADOW / GRADIENT"
    $title.TextFrame.TextRange.Font.Name = "Arial"
    $title.TextFrame.TextRange.Font.Size = 20
    $title.TextFrame.TextRange.Font.Bold = -1
    $weights = @(0.25, 0.5, 1.0, 2.0)
    for ($i = 0; $i -lt $weights.Count; $i++) {
        $y = 90 + 70 * $i
        $solid = $s1.Shapes.AddLine(70, $y, 430, $y)
        $solid.Line.Weight = $weights[$i]
        $solid.Line.ForeColor.RGB = 0x202020
        $dash = $s1.Shapes.AddLine(520, $y, 890, $y + 18)
        $dash.Line.Weight = $weights[$i]
        $dash.Line.DashStyle = 4
        $dash.Line.ForeColor.RGB = 0x0070C0
    }
    $divider = $s1.Shapes.AddLine(480, 65, 480, 505)
    $divider.Line.Weight = 0.5
    $divider.Line.DashStyle = 4
    $divider.Line.ForeColor.RGB = 0x000000
    $shadow = $s1.Shapes.AddShape(5, 90, 385, 270, 90)
    $shadow.Fill.ForeColor.RGB = 0x50C878
    $shadow.Line.Visible = 0
    $shadow.Shadow.Visible = -1
    $shadow.Shadow.Blur = 18
    $shadow.Shadow.OffsetX = 9
    $shadow.Shadow.OffsetY = 7
    $gradient = $s1.Shapes.AddShape(5, 600, 385, 270, 90)
    $gradient.Fill.ForeColor.RGB = 0xFF6600
    $gradient.Fill.BackColor.RGB = 0x0066FF
    $gradient.Fill.TwoColorGradient(1, 1)
    $gradient.Line.Visible = 0

    # Slide 2: alpha, crop, rotation, overlap and z-order.
    $s2 = $deck.Slides.Add(2, 12)
    $dark = $s2.Shapes.AddShape(1, 0, 0, 480, 540)
    $dark.Fill.ForeColor.RGB = 0x202020
    $dark.Line.Visible = 0
    $light = $s2.Shapes.AddShape(1, 480, 0, 480, 540)
    $light.Fill.ForeColor.RGB = 0xF4F4F4
    $light.Line.Visible = 0
    $pic1 = $s2.Shapes.AddPicture($alpha, 0, -1, 100, 90, 330, 330)
    $pic1.Rotation = 17
    $pic1.PictureFormat.CropLeft = 19
    $pic1.PictureFormat.CropTop = 11
    $pic2 = $s2.Shapes.AddPicture($alpha, 0, -1, 545, 95, 320, 320)
    $pic2.Rotation = -17
    $overlay = $s2.Shapes.AddShape(9, 360, 170, 240, 210)
    $overlay.Fill.ForeColor.RGB = 0xFF4040
    $overlay.Fill.Transparency = 0.45
    $overlay.Line.ForeColor.RGB = 0xFFFFFF
    $overlay.Line.Weight = 1.5
    $caption = $s2.Shapes.AddTextbox(1, 300, 450, 360, 40)
    $caption.TextFrame.TextRange.Text = "ALPHA / CROP / ROTATE / OCCLUSION"
    $caption.TextFrame.TextRange.Font.Name = "Arial"
    $caption.TextFrame.TextRange.Font.Size = 16

    # Slide 3: full bleed, adjacency and edge seams.
    $s3 = $deck.Slides.Add(3, 12)
    $left = $s3.Shapes.AddShape(1, 0, 0, 480, 540)
    $left.Fill.ForeColor.RGB = 0x005BBB
    $left.Line.Visible = 0
    $right = $s3.Shapes.AddShape(1, 480, 0, 480, 540)
    $right.Fill.ForeColor.RGB = 0xFFD500
    $right.Line.Visible = 0
    $hairline = $s3.Shapes.AddLine(480, 0, 480, 540)
    $hairline.Line.Weight = 0.25
    $hairline.Line.DashStyle = 4
    $hairline.Line.ForeColor.RGB = 0x000000
    $label = $s3.Shapes.AddTextbox(1, 250, 238, 460, 70)
    $label.TextFrame.TextRange.Text = "FULL BLEED / ADJACENCY / 0.25 pt DASH"
    $label.TextFrame.TextRange.Font.Name = "Arial"
    $label.TextFrame.TextRange.Font.Size = 18
    $label.TextFrame.TextRange.Font.Bold = -1

    $deck.SaveAs($output, 24)
}
finally {
    if ($deck) { $deck.Close() }
    if ($ppt) { $ppt.Quit() }
    foreach ($object in @($deck, $ppt)) {
        if ($object) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($object) }
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

