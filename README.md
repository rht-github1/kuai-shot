# 快截图

面向 Ubuntu 24.04 / GNOME Wayland 的区域截图工具。开机自启、全局快捷键、按当前屏幕现场探测，不绑定某台机器的分辨率或屏数。

## 功能

- 全局快捷键 **Ctrl+Alt+A**，托盘常驻
- 每次截图现场读取输出数量、位置、分辨率和缩放，1 块屏或 N 块屏走同一条路径
- 先抓图像素，再打开遮罩，避免拍到工具自己的全屏层
- 跨屏拖拽选区，导出时自动拼接
- 八向缩放，靠近边缘拖动可移动选区
- 点选窗口：能对上的会套住（接近铺满某块屏的窗口、以及坐标可信的小窗）；Wayland 不提供公开窗口位置，对不上的不会乱套，请改用拖选
- 矩形 / 椭圆 / 箭头 / 画笔 / 高亮 / 马赛克 / 文字
- 颜色、线宽、撤销 / 重做
- 复制到剪贴板；只有点保存才会写文件
- 钉在最上层，可拖动，右键或双击关闭
- 原图像素导出，不二次压缩、不拉伸降质

## 环境

- Ubuntu 24.04（Debian 系也可尝试）
- GNOME + Wayland
- `xdg-desktop-portal` 截图权限（安装脚本会写入）

换电脑、插拔显示器、改缩放后不用改配置，再按一次快捷键即可。

## 安装

全新系统只要能联网、有 `sudo`：

```bash
git clone https://github.com/rht-github1/kuai-shot.git
cd kuai-shot
./install.sh
```

脚本会：

1. 检测并 `apt` 安装缺失依赖（PyQt5、GTK、GStreamer、portal 等）
2. 复制到 `~/.local/share/kuai-shot`
3. 写入启动命令 `~/.local/bin/kuai-shot`
4. 写入登录自启、截图权限、GNOME 快捷键 `Ctrl+Alt+A`
5. 拉起托盘守护进程

已装过的机器再执行只会补缺包并覆盖程序文件。

时区或语言是国内时：

- `apt`：系统还在用官方源则临时走中科大 / 清华 / 阿里 / 华为，**不改** `/etc/apt`
- `pip`：本次安装走清华 / 阿里 / 中科大 / 华为 PyPI；若还没有 `~/.config/pip/pip.conf` 会写一份，已有配置不覆盖

GTK / GStreamer / portal 只能走 apt。`pip` 只用来在 apt 缺模块时补 `PyQt5`、`pycairo`。

```bash
KUAI_SHOT_SKIP_DEPS=1 ./install.sh                 # 跳过装包
KUAI_SHOT_APT_MIRROR=cn ./install.sh               # 强制国内 apt 镜像
KUAI_SHOT_APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn ./install.sh
KUAI_SHOT_APT_MIRROR=off ./install.sh               # 强制官方 apt 源
KUAI_SHOT_PIP_INDEX=cn ./install.sh                # 强制国内 PyPI
KUAI_SHOT_PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple ./install.sh
KUAI_SHOT_PIP_INDEX=off ./install.sh                # 强制官方 PyPI
KUAI_SHOT_PIP_WRITE_CONF=0 ./install.sh             # 不写 pip.conf
```

若快捷键未生效：设置 → 键盘 → 自定义快捷键，确认「快截图」为 `Ctrl+Alt+A`。
若命令找不到：把 `~/.local/bin` 加入 `PATH`，或重新登录。

## 操作

| 操作 | 说明 |
| --- | --- |
| 拖拽 | 选区，可从一块屏拖到另一块 |
| 单击窗口 | 套住能识别到的窗口 |
| 拖边缘 / 八角 | 移动或缩放选区 |
| 双击空白 / Enter（未选区） | 截取当前这块整屏 |
| 数字键 1-9 | 直接截取对应那块整屏 |
| Enter / 双击选区 / ✓ | 复制到剪贴板并关闭 |
| Esc / ✕ | 取消 |
| Ctrl+C / Ctrl+S | 复制 / 保存 |
| Ctrl+Z / Ctrl+Y | 撤销 / 重做 |
| [ / ] | 线宽 |
| 右键 | 取消 |
| 📌 | 复制并钉在最上层；钉图可拖，右键或双击关闭 |

托盘图标单击也可开始截图。保存前不会往「图片」目录自动落盘。

## 命令

```bash
kuai-shot            # 已有守护进程则直接返回；否则启动托盘
kuai-shot capture    # 通知守护进程开始截图（快捷键走这条）
kuai-shot ping       # 检查守护进程是否在运行
kuai-shot quit       # 退出守护进程
```
