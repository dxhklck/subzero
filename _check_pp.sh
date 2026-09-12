export PATH="/usr/bin:/bin:/c/Windows/System32:$PATH"
cd /g/video-subtitle-remover
echo "=== 1) ProPainter 显存决定因素 ==="
grep -n "propainterMaxLoadNum\|max_load_num\|use_ddp" backend/inpaint/propainter_inpaint.py | head -15
echo
echo "=== 2) 输入尺寸相关 ==="
grep -n "resize\|self\.size\|use_half\|fp16" backend/inpaint/propainter_inpaint.py | head -20
echo
echo "=== 3) 当前默认值 ==="
./python/python.exe -c "from backend.config import config; print('propainterMaxLoadNum current =', config.propainterMaxLoadNum.value)"
