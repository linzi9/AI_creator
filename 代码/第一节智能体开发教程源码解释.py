# 导入操作系统交互模块，用于环境变量、文件路径、工作目录等系统操作
import os
# 导入子进程管理模块，用于执行外部shell命令
import subprocess
# 从dataclasses模块导入dataclass装饰器，用于快速定义数据存储类，自动生成初始化方法。
#装饰器就是 Python 里的一个「功能增强工具」
#语法：用 @名字 写在 函数 / 类 的上方
#作用：不用修改原代码，就能给函数 / 类自动添加额外功能
#原本写一个类，需要自己写 __init__ 初始化方法
#加了 @dataclass 装饰器，自动生成初始化、打印等方法，省代码
#只有这个库专门写了装饰器，你才能导入使用；
#90% 的普通库（比如你代码里的 os/subprocess）没有装饰器，根本导不出来。
from dataclasses import dataclass

# 尝试导入readline模块，用于增强命令行输入体验（支持输入历史、行编辑功能）
try:
    import readline
    # 修复macOS系统上libedit版本readline的UTF-8退格问题（对应项目issue #143）
    # 关闭tty特殊字符绑定，避免特殊字符干扰UTF-8输入解析
    readline.parse_and_bind('set bind-tty-special-chars off')
    # 开启输入元字符支持，允许处理8位输入字符，为UTF-8编码提供支持
    readline.parse_and_bind('set input-meta on')
    # 开启输出元字符支持，确保UTF-8字符可以正确输出到终端
    readline.parse_and_bind('set output-meta on')
    # 关闭元字符自动转换，避免将高位UTF-8字符转义为ASCII转义序列，保留原始编码
    readline.parse_and_bind('set convert-meta off')
    # 开启元键绑定支持，启用Alt等组合键的编辑功能
    readline.parse_and_bind('set enable-meta-keybindings on')
# 如果导入readline失败（如Windows系统不支持该模块），则忽略该错误，不影响程序核心功能运行
except ImportError:
    pass

# 导入Anthropic官方Python SDK，用于调用Claude大模型的API服务
from anthropic import Anthropic
# 导入dotenv模块的load_dotenv函数，用于从本地.env文件加载环境变量，方便本地开发配置
from dotenv import load_dotenv

# 加载.env文件中的环境变量，override=True表示如果系统环境变量中已存在同名变量，则用.env中的配置覆盖，确保使用本地配置
load_dotenv(override=True)

# 如果用户配置了自定义的Anthropic API基础地址（如使用代理服务、自托管兼容接口）
if os.getenv("ANTHROPIC_BASE_URL"):
    # 移除原有的Anthropic官方认证Token，避免与自定义服务的认证逻辑产生冲突
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

# 初始化Anthropic API客户端，如果配置了自定义base_url则使用该地址，否则使用官方默认地址
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))

# 从环境变量中获取要使用的Claude模型ID（如claude-3-opus-20240229），该配置为必填项
MODEL = os.environ["MODEL_ID"]

# 定义发送给Claude的系统提示词，设定Agent的身份与行为规则
SYSTEM = (
    # 告知Agent当前的工作目录位置，让它明确操作的上下文
    f"You are a coding agent at {os.getcwd()}. "
    # 指示Agent的工作方式：优先使用bash工具检查和修改工作区，先执行操作，之后再清晰向用户报告结果
    "Use bash to inspect and change the workspace. Act first, then report clearly."
)

# 定义Claude可以使用的工具列表，这里仅提供bash命令执行工具，符合Anthropic工具调用的API格式
TOOLS = [{
    "name": "bash",  # 工具名称，Claude会通过这个名称识别并调用该工具
    "description": "Run a shell command in the current workspace.",  # 工具描述，告诉Claude这个工具的作用
    # 工具的输入参数Schema，定义工具需要的输入格式，符合Anthropic API的要求
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string"}},  # 输入需要一个名为command的字符串参数，即要执行的shell命令
        "required": ["command"],  # 标记command为必填参数，确保Claude调用工具时必须传入命令
    },
}]

# 使用dataclass装饰器，将LoopState类转换为数据类，自动生成__init__、__repr__等方法，简化状态存储的代码
#LoopState = AI 智能体的「工作备忘录 / 状态记录本」
#这个智能体需要循环思考、执行命令（比如调用 bash、改代码），
#它必须记住「自己做到哪了、说了什么、执行了几步」，
#这些信息全部存在 LoopState 里。
@dataclass
class LoopState:
    # 最小化的Agent循环状态，包含对话历史、循环轮次、状态转换原因
    messages: list  # 对话历史消息列表，存储用户、AI、工具调用的所有交互记录，用于给Claude提供上下文
    turn_count: int = 1  # 当前已经执行的循环轮次，默认从1开始计数，用于统计交互次数
    # str | None （Python 类型注解）
    # 这是类型提示，规定这个变量只能存两种值：
    # str = 字符串（比如 "tool_result"）
    # None = 空值（代表「没有原因」）
    # | = 或者 的意思
    # 大白话：这个变量要么存文字，要么是空的，不能存数字 / 列表等其他东西
    transition_reason: str | None = None  # 状态转换的原因，标记为什么要继续/停止循环，初始为None

def run_bash(command: str) -> str:
    """
    执行bash命令的工具函数，包含安全检查与错误处理
    :param command: 要执行的shell命令字符串
    :return: 命令执行的输出结果字符串，或错误信息
    """
    # 定义危险命令列表，阻止AI执行可能破坏系统的高危操作，做安全防护
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    # 检查输入的命令中是否包含任何危险命令片段
    if any(item in command for item in dangerous):
        return "Error: Dangerous command blocked"  # 如果检测到危险命令，直接返回错误，阻止执行
    
    try:
        # 使用subprocess.run执行shell命令
        result = subprocess.run(
            command,  # 要执行的命令内容
            shell=True,  # 使用系统shell解析命令，支持管道、重定向等完整的shell语法
            cwd=os.getcwd(),  # 指定命令的执行目录为当前工作目录，确保命令在正确的路径下运行
            capture_output=True,  # 捕获命令的标准输出和标准错误，用于后续返回给AI
            text=True,  # 将输出转换为字符串格式，而非原始bytes，方便处理
            timeout=120,  # 设置命令超时时间为120秒，防止命令长时间卡死阻塞Agent
        )
    # 捕获命令超时异常，处理命令运行时间过长的情况
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    # 捕获文件不存在或系统调用错误异常，处理命令执行的系统级错误
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"
    
    # 合并标准输出和标准错误，去除首尾空白字符，统一返回所有输出
    output = (result.stdout + result.stderr).strip()
    # 如果输出过长，截断到50000字符，避免输出太多超出API的输入长度限制；如果没有输出则返回提示
    return output[:50000] if output else "(no output)"

def extract_text(content) -> str:
    """
    从Claude的响应内容中提取纯文本内容，过滤掉工具调用等非文本块
    :param content: Claude返回的content字段，可能是列表或其他类型
    :return: 提取出的纯文本字符串
    """
    # 如果content不是列表（非预期的格式），直接返回空字符串，做容错处理
    if not isinstance(content, list):
        return ""
    texts = []
    # 遍历content中的每个内容块，Claude的响应content是多块结构，可能包含文本块、工具调用块等
    for block in content:
        # 获取块的text属性，如果存在则说明这是一个文本块
        text = getattr(block, "text", None)
        if text:
            texts.append(text)
    # 将所有文本块拼接，去除首尾空白后返回，得到纯文本的最终回复
    return "\n".join(texts).strip()

def execute_tool_calls(response_content) -> list[dict]:
    """
    执行Claude请求的工具调用，处理bash命令并返回符合API要求的结果
    :param response_content: Claude返回的响应内容列表
    :return: 工具调用的结果列表，符合Anthropic API的消息格式
    """
    results = []
    # 遍历响应中的每个内容块
    for block in response_content:
        # 如果当前块不是工具调用类型，跳过这个块
        if block.type != "tool_use":
            continue
        # 从工具调用的输入参数中获取要执行的bash命令
        command = block.input["command"]
        # 打印黄色的命令执行提示，\033[33m是ANSI终端颜色码，设置为黄色，\033[0m重置颜色，让用户直观看到AI正在执行的命令
        print(f"\033[33m$ {command}\033[0m")
        # 调用run_bash执行命令，获取命令的输出结果
        output = run_bash(command)
        # 打印命令输出的前200字符，让用户快速看到命令的执行结果，不用等待全部输出
        print(output[:200])
        # 将工具调用结果整理成Anthropic API要求的格式
        results.append({
            "type": "tool_result",  # 标记这是一个工具调用结果
            "tool_use_id": block.id,  # 关联对应的工具调用ID，让API能匹配工具请求和响应，保证上下文正确
            "content": output,  # 工具执行的输出内容，返回给Claude作为下一步的上下文
        })
    # 返回所有工具调用的结果
    return results

def run_one_turn(state: LoopState) -> bool:
    """
    执行Agent的单轮交互：调用Claude API，处理工具调用，更新状态
    :param state: 当前的Agent循环状态
    :return: 是否需要继续下一轮循环（True=继续，False=结束）
    """
    # 调用Anthropic的messages API，发送请求获取Claude的响应
    response = client.messages.create(
        model=MODEL,  # 指定要使用的Claude模型
        system=SYSTEM,  # 系统提示词，定义Agent的行为
        messages=state.messages,  # 完整的对话历史消息，给Claude提供上下文
        tools=TOOLS,  # 告知Claude它可以使用的工具列表
        max_tokens=8000,  # 设置响应的最大token数，支持长输出，满足复杂任务的需求
    )
    # 将Claude的响应添加到对话历史中，作为assistant角色的消息，更新上下文
    state.messages.append({"role": "assistant", "content": response.content})
    
    # 如果Claude停止生成的原因不是工具调用，说明它已经完成了任务，不需要再执行工具了
    if response.stop_reason != "tool_use":
        state.transition_reason = None
        return False  # 返回False，结束循环，任务完成
    
    # 否则，执行Claude请求的工具调用，处理它要运行的bash命令
    results = execute_tool_calls(response.content)
    # 如果没有得到任何工具结果，说明没有可执行的工具，结束循环
    if not results:
        state.transition_reason = None
        return False
    
    # 将工具调用的结果添加到对话历史中，作为user角色的消息，这是Anthropic API要求的格式
    state.messages.append({"role": "user", "content": results})
    # 循环轮次加1，统计交互次数
    state.turn_count += 1
    # 标记状态转换原因为工具结果，说明是因为有工具执行结果所以要继续下一轮循环
    state.transition_reason = "tool_result"
    # 返回True，继续下一轮循环，把工具结果发给Claude继续处理
    return True

def agent_loop(state: LoopState) -> None:
    """
    Agent的主循环，不断执行单轮交互，直到Claude不再需要调用工具，完成用户的任务
    :param state: Agent的初始状态
    """
    # 循环调用run_one_turn，直到它返回False，也就是直到Claude完成任务不再需要调用工具
    while run_one_turn(state):
        pass

#1. Python 程序的「主入口」判断。
#if __name__ == "__main__": 到底是什么？
#一句话总结：
#它是 Python 程序的「主入口开关」
#只有直接运行这个代码文件时
#它下面的代码才会执行
#如果这个文件被当成模块导入到别的代码里
#它下面的代码不会执行
if __name__ == "__main__":
    # 2. 初始化一个空列表，用来存储**完整的对话历史**
    history = []
    # 3. 开启无限循环：程序会一直运行，直到触发退出条件
    while True:
        # 4. try：尝试执行下面的代码，捕获可能的异常
        try:
            # 5. 接收用户的命令行输入，带青色提示符 s01 >>
            #\033[36ms01 >> \033[0m 是终端彩色文字的控制代码（专业名叫 ANSI 转义序列）
            #不是 Python 语法，是给命令行 / 终端看的，作用只有一个：
            #让终端里的提示符 s01 >> 变成青蓝色，输完颜色自动恢复默认。
            #1. \033[36m → 开启青色
            #\033：终端的转义开始标记（告诉终端：我要改样式了）
            # [36m：颜色代码，36 = 青蓝色（也叫天蓝色 / 青色）
            # 2. s01 >> → 纯文字
            # 就是你要显示的提示符，会被染成上面设置的青色。
            # 3. \033[0m → 关闭颜色，恢复默认
            # 必须加！否则终端后面所有文字都会变成青色
            # [0m = 重置所有样式（颜色、加粗等全部恢复默认）
            #弹出一个青蓝色的 s01 >> 提示符，等待你输入文字。
            query = input("\033[36ms01 >> \033[0m")
        # 6. 捕获两种退出异常，捕获到就退出循环
        except (EOFError, KeyboardInterrupt):
            break
        # 7. 判断用户输入：如果是 q/exit/空字符，退出循环
        #query 就是你刚才在终端里输入的那句话。
        #.strip()的作用：去掉字符串前后的空格、换行、空白
        #.lower()把所有字母变小写
        #in 就是：是不是在这个列表里，只要满足条件之一就算是
        if query.strip().lower() in ("q", "exit", ""):
            break
        # 8. 把用户的输入，以固定格式添加到对话历史
        history.append({"role": "user", "content": query})
        # 9. 创建一个状态对象，把对话历史传进去
        state = LoopState(messages=history)
        # 10. 调用核心代理逻辑，处理对话状态
        agent_loop(state)
        # 11. 从对话历史最后一条（助手回复）中提取纯文本
        final_text = extract_text(history[-1]["content"])
        # 12. 如果提取到了文本，就打印输出
        if final_text:
            print(final_text)
        # 13. 打印一个空行，美化输出格式
        print()









