# Personal Research GUI and Calculators

个人科研工具箱。**PySide6** 桌面程序，独立于 `myapp`，两者互不影响。

**界面全英文**；源码注释保留中文（便于维护）。

所有数学/物理计算来自经双路验证的纯逻辑层（`core/`），界面只负责交互与呈现。

## 启动

### 第一次（任一机器）

1. 双击 **`setup.bat`**
   - 建项目自己的 `.venv`
   - 装 `requirements.txt` 里的依赖（约 250 MB，第一次几分钟）
   - 末尾会自检一遍所有模块能否导入
2. 双击 **`run.bat`** 启动

`run.bat` 会优先用项目内的 `.venv`，没有时回退到系统 Python。

只想开一个设备（不启动主界面）：

```bat
device.bat stage      等价于  python -m devicegui.stage_app
device.bat daq        等价于  python -m devicegui.daq_app
```

### 搬到另一台机器（比如实验室电脑）

项目是**自包含**的：源码 + `setup.bat` 就能在任意机器上重建环境。

**有网的情况**

1. 整个 `research-gui` 文件夹拷过去
   - **不要**拷 `.venv/` —— 虚拟环境绑定绝对路径，换机器/换路径会失效
   - 可以不拷 `wheels/`（体积大，新机器能自己下载）
2. 在新机器上双击 `setup.bat`
3. 双击 `run.bat`

**实验室机器没网**

1. 先在有网的机器上双击 **`download_wheels.bat`**
   - 把所有依赖下到项目里的 `wheels/` 文件夹
2. 把整个文件夹（**这次要包含 `wheels/`**）拷到实验室电脑
3. 在那儿双击 `setup.bat` —— 它会检测到 `wheels/` 并**离线安装**

### 实验室机器还需要另外装什么

| 用途 | 需要什么 |
|---|---|
| 界面 + 计算（Calculus / Plotter / Matrix / Ray） | 只要跑过 `setup.bat` |
| ASI 位移台 | USB 转串口驱动（CH340 / FTDI，看线） |
| GPIB 仪器 | NI-VISA，或其它 VISA 实现 |
| NI-DAQ 板卡 | **NI-DAQmx 驱动**（从 NI 官网装；pip 的 `nidaqmx` 只是 Python 接口，不含驱动） |

驱动缺失时程序不会崩，会在连接失败的提示里说明缺哪一样。

### VS Code

项目里带好了 `.vscode/` 配置和 `research-gui.code-workspace`：

- 双击 `research-gui.code-workspace` 打开
- 解释器自动指向 `${workspaceFolder}\.venv`（不写死机器路径）
- **F5** 直接运行 `main.py`（三种调试配置：主程序 / 当前文件 / 测试）
- **Ctrl+Shift+B** 跑 `Run GUI` 或 `Check imports`
- 首次打开可能提示安装 Python 扩展，装 `ms-python.python` 即可

## 版本管理（git）

项目已经是一个 git 仓库。`.gitignore` 排除了 `.venv/`、`wheels/`、`__pycache__/`，
所以仓库只有约 **150 KB**，克隆很快。

### 在实验室电脑上 clone

仓库的「远程」是本机的一个 bare 仓库：

```
C:\Users\Deliv\git-repos\research-gui.git
```

1. 把这个 `research-gui.git` 文件夹拷到移动硬盘 / U 盘
2. 实验室电脑上（假设盘符是 `E:`）：

```bat
git clone E:\research-gui.git
cd research-gui
setup.bat
run.bat
```

`setup.bat` 会在克隆出来的目录里重新建 `.venv`，所以**不需要**从这台机器拷依赖。

### 日常改代码

在本机改完，提交并同步到 bare 仓库：

```bat
git add -A
git commit -m "说说改了什么"
git push
```

实验室那边：

```bat
git pull
```

### 想换成 GitHub（可选，长期更省心）

1. 在 GitHub 建一个**空**仓库（不要勾 "Add a README"）
2. 在本机项目目录执行：

```bat
git remote set-url origin https://github.com/<你的用户名>/research-gui.git
git push -u origin main
```

3. 之后实验室电脑直接：

```bat
git clone https://github.com/<你的用户名>/research-gui.git
```

好处是任何地方都能克隆、有云端备份；代价是仓库需要是 **private**（除非你不在乎公开）。

### 注意

- git 身份目前是占位的：

  ```
  user.name  = Deliv
  user.email = deliv@users.noreply.github.com
  ```

  想改成自己的真实邮箱：

  ```bat
  git config --global user.email "you@example.com"
  git config --global user.name "Your Name"
  ```

- `.git` 目录也在 OneDrive 里会同步，但它只有 150 KB 左右，无所谓。

## 依赖

`setup.bat` 会自动装。手动装：`pip install -r requirements.txt`

```
界面    PySide6-Essentials   （只要 Essentials；完整 PySide6 会多拉
                               WebEngine/Pdf/Addons，胖约 180 MB 且用不到）
计算    numpy  scipy  matplotlib  sympy  pandas
硬件    pyserial  pyvisa  pyvisa-py  nidaqmx
```

硬件那几个只有接了对应仪器才真正需要，但**一并装上没有副作用** ——
缺驱动时只是连接失败，不会崩。

### 一个坑：别把 .venv 放在 OneDrive 里同步

如果项目目录在 OneDrive 下（比如桌面），`.venv` 会被一起上传，
体积几百 MB 且每次装包都要重新同步。两个选择：

1. **推荐**：把项目移到非同步目录，例如 `C:\Users\Deliv\research-gui`，
   再到 OneDrive 设置里排除该文件夹
2. 或者保持位置，但在 OneDrive 的「选择文件夹」里取消勾选项目下的 `.venv`

源码本身很小（几百 KB），随便同步没问题；只是虚拟环境不值得同步。

## 结构

```
research-gui/
├── main.py                  入口（先锁 QT_API=pyside6，再设 matplotlib QtAgg）
├── run.bat                  双击启动
├── core/                    纯逻辑，无 GUI 依赖，可单独测试
│   ├── expr_eval.py         表达式求值（AST 白名单，绝不 eval）
│   ├── latex_render.py      LaTeX -> 透明 PNG
│   ├── calculus_numeric.py  数值积分 / 导数 / 偏导（复数安全）
│   ├── calculus_symbolic.py 符号积分 / 求导（sympy，带变量假设）
│   ├── optics/              光学物理核心（光线追迹、焦距、光斑、球差）
│   ├── eigencalc.py         特征值 / 特征函数 / 矩阵运算
│   ├── csvdata.py           CSV 读取与绘图
│   ├── transport.py         传输层：Serial / LAN / GPIB
│   ├── devices.py           设备注册表（下拉里的型号）
│   ├── nidaq.py             NI-DAQ 模拟输出（DC 电压）
│   └── stage.py             ASI LX-4000 位移台
├── devicegui/             各设备 GUI（每个都能独立运行）
│   ├── base.py             设备清单 + 窗口构造 + 独立运行入口
│   ├── stage_app.py        python -m devicegui.stage_app
│   ├── daq_app.py          python -m devicegui.daq_app
│   ├── camera_app.py / laser_app.py / scope_app.py / others_app.py
│   └── ...
│
├── device.bat              单独启动某个设备 GUI
│
└── ui/
    ├── main_window.py       主窗口 + 左侧导航 + QSS
    ├── workers.py           后台任务（QThreadPool 封装）
    ├── widgets/             device_selector.py 等复用组件
    └── pages/
        ├── home_page.py        首页（标题 + 功能卡片）
        ├── calculus_page.py    微积分（积分 / 导数 / 偏导）
        ├── formula_page.py     公式渲染
        ├── ray_page.py         光线追迹（光线图 / 像质分析）
        ├── matrix_page.py      矩阵计算器（4 个标签页）
        ├── plotter_page.py     CSV 绘图
        ├── stage_page.py       ASI LX-4000 位移台控制
        ├── daq_page.py         NI-DAQ 电压输出
        └── placeholder_page.py Device Interface 其余子分类的占位
```

## 导航

左侧是**树形菜单**（可折叠）。启动进入 **Home**。

```
🏠  Home
∫   Calculus
∑   Formula Renderer
◇   Ray Tracer
▦   Matrix Calculator
📈  Plotter
⚙   Device Interface          ← 分组，点标题看概览
      ⇔  Stage Controller
      ◉  Camera
      ✳  Laser
      ∿  Oscilloscope
      ⧉  Data Acquisition
      ⋯  Others
```

| 菜单项 | 状态 |
|---|---|
| Home | 首页：标题 + 功能卡片（点击即跳转） |
| Calculus | 已完成 |
| Formula Renderer | 已完成 |
| Ray Tracer | 已完成 |
| Matrix Calculator | 已完成（4 个标签页） |
| Plotter | 已完成 |
| Device Interface | 分组节点，显示概览 |
| └ Stage Controller | **已完成**（ASI LX-4000） |
| └ Data Acquisition | **已完成**（NI PCI-6229 / BNC-2120） |
| └ Camera / Laser / Oscilloscope / Others | **占位**，等确定仪器型号与命令集 |

## 已完成的功能页

### Calculus
- **Integral**：留空上下限 = 不定积分；给出上下限 = 定积分（同时给数值与符号结果）
- **Derivative**：一阶/二阶，指定求值点
- **Partial**：多变量，求值点写成 `x=1, y=2`，可指定对哪个变量求导
- 每个页面都支持 **Assumption**：Real / Integer / Positive / Non-negative / Complex
  （选 Integer 时 `e^(2πin)` 这类会化简）
- 结果三处同显：LaTeX 图 + 可复制的 LaTeX 源码 + 数值文本

### Formula Renderer
输入 LaTeX → 渲染成透明背景 PNG，可调字号/颜色，能复制源码或另存 PNG。

### Ray Tracer
- 左侧面型表可直接编辑（Name / R / Spacing / n / Semi-ap.），支持 Add、Delete last、Load defaults
- **Ray diagram**：真实折射路径，被孔径拦截的光线打叉
- **Image quality**：球差曲线（LSA vs 瞳孔高度）
- 底部汇总：EFL / BFL / FFL / 主平面 / 轴上光斑 RMS·PV / 最佳焦面 / 光线到达情况

默认系统（6 面三片式）的基准值：
```
EFL = 68.8569 mm   BFL = 41.2968 mm   FFL = 70.0122 mm
principal planes: front -1.155 / rear -27.560 mm
on-axis spot RMS 177.11 µm / PV 727.32 µm
```

### Matrix Calculator
四个标签页，对应 eigencalc_gui.py 的四个 tab：

- **Matrix eigenvalues**：数值（含 Hermitian 判定、残差、病态警告）与符号
  （特征多项式、有理因式分解、特征值/重数、n≤2 的特征向量）双路
- **Matrix operations**：A+B, A−B, A·B, A·v, A⁻¹·v, A⁻¹·B, A⁻¹, det(A)；
  可选 decimal result / simplify symbolic result
- **1D eigenfunctions**：有限差分解 `-u'' + V(x)u = λu`（Dirichlet），
  有 SciPy 时用三对角求解器只算最低 k 个；画出 V(x) 与各能级的特征函数
- **Eigenvalues vs parameter**：扫一个符号，画能级或特征态的分量权重，
  可导出 CSV

输入约定（沿用原版）：
- `^` 或 `**` 表示乘方；隐式乘法可用（`2x`、`2pi`、`sin x`）
- **多字母名是一个符号**（`mass`、`lam`、`beta`）；要乘积请写 `x*y`
- 任意单元格粘贴 Excel 块（`1 2; 3 4` 或制表符分隔）会自动扩展并填充

验证（对解析解）：
```
谐振子 V=x²     λ = 0.99997, 2.99987, 4.99967, 6.99937    (解析 1,3,5,7)
方势井 V=0      λ = 9.8696, 39.47833, 88.82598            (解析 (nπ)²)
符号特征值      λ₁ = -1, λ₂ = 3
参数扫描        λ = u∓1
```

### Device Interface：分类入口，而不是内嵌面板

主 GUI **不内嵌**任何设备界面，只负责分类与启动。每个设备都是一个独立 GUI：

```
主 GUI (main.py)                    独立设备 GUI（各自一个进程）
├─ Calculus                            
├─ Ray Tracer                          python -m devicegui.stage_app
├─ Matrix / Plotter          ┌──────▶  python -m devicegui.daq_app
└─ Device Interface ─────────┤        python -m devicegui.camera_app
   （六张入口卡片）          │        ...
                             └──────▶ 也可以从 device.bat 启动
```

**Device Interface 页面上能看到什么**

每张卡片显示：

| 元素 | 说明 |
|---|---|
| 图标 + 名称 | 例如 `⇔ Stage Controller` |
| 一句话说明 | 这个仪器是干什么的 |
| 启动命令 | `python -m devicegui.stage_app`，可直接复制到终端 |
| 运行状态 | `● Not running` / `● Running (pid 1234)` / `● Stopped` |
| Launch / Stop | 启动或结束该设备窗口 |

未实现的分类带 `placeholder` 标记，点开是说明页（会告诉我还缺什么信息）。

**两种使用方式**

1. **从主 GUI**：点 Launch —— 会起一个子进程，多个设备窗口可以同时开着
2. **单独运行**：`python -m devicegui.<name>` 或 `device.bat <name>`

两种方式跑的是**同一套代码**（都走 `devicegui/base.py` 的 `create_device_window`），
所以界面和逻辑完全一致。

**为什么用独立进程**

- 一个设备驱动崩了不会带走主界面
- 设备窗口可以自由摆放、同时开着
- 主窗口关掉后，手动启动的设备窗口仍然在跑

主窗口关闭时会一并结束**由它启动的**那些子进程（避免留孤儿）；手动启动的不受影响。

### 连接设置对话框

```
Placeholder device
Method:  [ Serial (COM) / LAN (TCP/IP) / GPIB · VISA ▼ ]

┌──────────────────────────────────────────┐
│ Port: [COM6 ▼] [↻]   Baud: [115200 ▼]    │   ← Serial
│ Host: [192.168.1.100] Port: [5025]       │   ← LAN
│ Address: [GPIB0::12::INSTR] [List]       │   ← GPIB
└──────────────────────────────────────────┘
                              [ OK ] [ Cancel ]
```

- **Method** 下拉只列出该设备型号支持的方式
- 参数区随方式切换
- 每种方式的配置**分别记住**（串口的 `port` 是 COM6，网口的 `port` 是 5025，
  同名不同义，混用会解析失败 —— 所以按方式分开存）
- 设备切换回之前用过的型号时，原来的配置还在

### Data Acquisition
NI PCI-6229 + BNC-2120 的模拟 DC 电压输出（用来驱动激光功率）：

- **设备**：`NI PCI-6229 (BNC-2120)` 或 `Placeholder device`（无硬件时试流程）
- **连接方式**：`Local board (NI-DAQmx)`，只需资源名（如 `Dev1`）
- **输出**：通道名（连接后可锁定）、0–5 V 电压（数字框 + 滑块联动）、
  Live update（拖动时实时输出）、Apply / Stop
- **Stop** 会先写 0 V 再释放 DAQ task

#### 激光安全（重要）

原工具最值得保留的就是这一层，这里完整保留并做成可测试的纯逻辑：

- 电压 **≥ 1.0 V** 且本次会话尚未确认 → 弹出 `LASER SAFETY` 确认框，
  默认按钮是 **No**
- 用户拒绝 → 输出取消，滑块**退回上一次成功输出的值**
- 用户同意 → 记录确认，之后同一会话内不再重复询问
- 电压**降到阈值以下会重新武装** —— 下次再升上去还要再确认一次
- 当前电压 ≥ 阈值时，界面上常驻一条红色横幅提醒

阈值、警告文案都放在 `core/nidaq.py` 的类常量里，改起来是一处。

#### 没有 NI-DAQmx 驱动时

Python 包 `nidaqmx` 可以单独安装，但**驱动必须另外装**（National Instruments）。
两者缺一，连接会给出明确提示：

```
nidaqmx is not installed (pip install nidaqmx)
NI-DAQmx driver not found. Install it from National Instruments ...
```

### 单文件版（拷到别的机器最省事）

`stage_standalone.py` 把 Stage Controller 用到的全部模块内联进了**一个文件**
（约 2800 行 / 98 KB），不需要项目目录结构：

```bash
pip install PySide6-Essentials pyserial
python stage_standalone.py
```

它由脚本生成，不要直接改：

```bash
python tools/build_stage_standalone.py
```

改了 `core/` 或 `ui/pages/stage_page.py` 之后重新跑一次即可。
生成物也提交在仓库里，所以可以直接从 GitHub 下载这一个文件带走。

覆盖范围：只有 Stage Controller（串口 / LAN / GPIB / 占位设备都保留）。
主 GUI 的其它页面（Calculus、Ray Tracer 等）不在里面。

### 传输层

`core/transport.py` 把「怎么连」与「说什么协议」分开，三种实现共用一套接口
（`open/close/read_until/write/in_waiting/reset_buffers`）：

| 实现 | 用什么 | 依赖 |
|---|---|---|
| `SerialTransport` | pyserial | pyserial |
| `LanTransport` | 原始 TCP socket（SCPI-RAW） | 标准库 |
| `GpibTransport` | pyvisa / VISA | pyvisa（+ NI-VISA 或 pyvisa-py 后端） |
| `LocalTransport` | 厂商驱动直接访问板卡（携带资源名如 `Dev1`） | 厂商库（如 nidaqmx） |

所以 `ASI_lx_4000` 只写 ASI 协议，不关心底下是串口还是网口。

### 加一个新设备

1. 写驱动类（只依赖 `Transport`，不直接碰 pyserial）
2. 在 `core/devices.py` 里加一条 `DeviceSpec`：
   ```python
   DeviceSpec(key="my_stage", name="My Stage", transports=("serial", "lan"),
              description="...", factory=lambda tr, **o: MyStage(tr, **o))
   ```
3. 下拉里就会出现它 —— 界面代码不用改

### Stage Controller
ASI LX-4000 二维位移台（串口）：

- **连接**：设备下拉（自动扫描端口）+ 波特率 + Connect / Disconnect
- **位置**：X / Y 实时读数（单位 0.1 µm），手动 Refresh
- **移动**：绝对位置（Go X / Go Y）、步进 jog（X± / Y±，步长可调）
- **动作**：HALT（急停）、Define origin（把当前位置设为原点）、
  Center and zero（回负限位 → 实测中心 → 重定义原点）
- **日志**：每条连接尝试、串口收发错误都带时间戳显示

安全设计：
- **HALT 不受忙碌状态限制**，随时可按（急停不能被 UI 锁挡住）
- **Center and zero 必须二次确认** —— 它会驱动到负限位，可能撞镜头
- 所有串口操作在后台线程，界面不会冻结
- 断开页面时自动 halt + 关闭端口，避免端口被占住

协议（沿用原实现）：命令前缀 `2H `、`\r` 结尾；`:A` 接受 / `:N` 拒绝；
`STATUS` 返回 `N` 空闲、`B` 运动中。

### Plotter
读 CSV / TSV 并画图：

- **两种选列方式**：一个 X 配多列 Y，或若干独立的 X/Y 对（每对可自定义图例）
- **布局**：合成一张图，或堆叠成多子图（可共享 X 轴）
- **五种绘图类型**：line / scatter / bar / step / stem
- **控制项**：标题、轴标签、轴范围、刻度步长、对数轴、网格、图例位置、
  标记形状、线型、标记大小、图形尺寸
- matplotlib 导航工具栏（缩放、平移、保存图片）
- 分隔符自动检测；`.tsv` 按制表符读

## 正确性

`core/` 与 `myapp` 的实现逐位一致，已用同一套光学系统对拍验证：

| 项目 | 结果 |
|---|---|
| `vertex_positions` / `system_matrix` / `focal_properties` | 完全一致 |
| `trace_rays`（光线数、逐点路径、每条状态） | 完全一致 |
| `spot_analysis` / `best_focus` / `longitudinal_spherical` | 完全一致 |
| 微积分数值（含复数积分 `∫₀^π e^(ix)dx = 2i`） | 一致 |
| 符号积分/求导、变量假设 | 一致 |

验证脚本见工作区 `test_research_core.py` / `test_eigencalc_core.py` / `test_csvdata_core.py`。

## 加新功能的方式

- **新页面**：在 `ui/pages/` 加一个类 → 在 `main_window.py` 的 `PAGES` 里加一行
  （格式 `(key, 显示名, 图标, "模块路径:类名")`）
- **新算法**：放进 `core/`，不碰界面
- 页面在第一次点开时才构造（懒加载），启动不会卡

## 已知取舍

- 光线追迹是**二维子午**模型：不支持非球面、色散、衍射、偏心
- 面型表编辑后需点 Re-trace（或切换视图）才生效
- matplotlib 中文标签依赖系统里有中文字体（Microsoft YaHei / SimHei），找不到时自动退回英文
- Device Interface 下只有 Stage Controller 已实现，其余 5 类仍是占位页
