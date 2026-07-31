"""
XiYan API 测试脚本 v4 - 基于参考项目的正确调用方式

关键修复：
- action: RunDataAnalysis (不是 RunSqlGeneration)
- pathname: /{workspace_id}/gbi/runDataAnalysis
- style: RPC (不是 ROA)
- body_type: sse (不是 json)
- 事件解析: res.get('event').data
"""
import asyncio
import json
import sys
import os
from datetime import datetime

# 创建测试输出目录
TEST_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(TEST_OUTPUT_DIR, exist_ok=True)

LOG_FILE = os.path.join(TEST_OUTPUT_DIR, f"xiyan_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

def log(msg: str):
    print(msg)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv
load_dotenv()

ACCESS_KEY_ID = os.getenv("XIYAN_ACCESS_KEY_ID", "")
ACCESS_KEY_SECRET = os.getenv("XIYAN_ACCESS_KEY_SECRET", "")
WORKSPACE_ID = os.getenv("XIYAN_WORKSPACE_ID", "llm-ckpxv751l1lrb0v2")
AGENT_KEY = os.getenv("XIYAN_AGENT_KEY", "9bd34592069b4cfa9b4afbee24f51edc_p_efm")
ENDPOINT = os.getenv("XIYAN_ENDPOINT", "dataanalysisgbi.cn-beijing.aliyuncs.com")


async def test_xiyan_correct_api(question: str):
    """使用正确的 API 调用方式测试 XiYan"""
    from alibabacloud_tea_openapi_sse.client import Client as OpenApiClient
    from alibabacloud_tea_openapi_sse import models as open_api_models
    
    # 尝试导入正确的 util_models
    try:
        from alibabacloud_tea_util_sse import models as util_models
        log("✅ 使用 alibabacloud_tea_util_sse")
    except ImportError:
        from alibabacloud_tea_util import models as util_models
        log("⚠️ 回退到 alibabacloud_tea_util")
    
    log("=" * 70)
    log("XiYan API 测试 (正确格式)")
    log(f"时间: {datetime.now().isoformat()}")
    log("=" * 70)
    log(f"Endpoint: {ENDPOINT}")
    log(f"Workspace ID: {WORKSPACE_ID}")
    log(f"Agent Key: {AGENT_KEY}")
    log(f"问题: {question}")
    log("-" * 70)
    
    if not ACCESS_KEY_ID or not ACCESS_KEY_SECRET:
        log("❌ 未配置 ACCESS_KEY")
        return
    
    # 创建客户端
    config = open_api_models.Config(
        access_key_id=ACCESS_KEY_ID,
        access_key_secret=ACCESS_KEY_SECRET
    )
    config.endpoint = ENDPOINT
    client = OpenApiClient(config)
    
    # ⭐ 正确的 API 参数 (来自参考项目)
    api_info = open_api_models.Params(
        action='RunDataAnalysis',          # 关键：正确的 action
        version='2024-08-23',
        protocol='HTTPS',
        method='POST',
        auth_type='AK',
        style='RPC',                        # 关键：RPC 而不是 ROA
        pathname=f'/{WORKSPACE_ID}/gbi/runDataAnalysis',  # 关键：包含 workspace_id
        req_body_type='json',
        body_type='sse'                     # 关键：sse 而不是 json
    )
    
    # 请求体
    body = {
        'specificationType': 'STANDARD_MIX',
        'query': question,
        'agentKey': AGENT_KEY
    }
    
    log(f"\n请求参数:")
    log(f"  action: {api_info.action}")
    log(f"  pathname: {api_info.pathname}")
    log(f"  style: {api_info.style}")
    log(f"  body_type: {api_info.body_type}")
    log(f"\n请求体:")
    log(json.dumps(body, ensure_ascii=False, indent=2))
    
    # 运行时配置
    runtime = util_models.RuntimeOptions()
    runtime.read_timeout = 100000
    runtime.connect_timeout = 10000
    
    # 构建请求
    request = open_api_models.OpenApiRequest(body=body)
    
    log("\n正在调用 XiYan API...")
    log("-" * 70)
    
    sql_result = None
    error_result = None
    
    try:
        # 调用 SSE API
        sse_receiver = client.call_sse_api_async(
            params=api_info, 
            request=request, 
            runtime=runtime
        )
        
        event_count = 0
        async for res in sse_receiver:
            event_count += 1
            log(f"\n[事件 #{event_count}]")
            log(f"  res 类型: {type(res)}")
            
            # ⭐ 参考项目的解析方式
            try:
                # 方式1: res.get('event') - 参考项目的方式
                if isinstance(res, dict):
                    event_obj = res.get('event')
                    if event_obj is not None and hasattr(event_obj, 'data'):
                        raw_data = event_obj.data
                        log(f"  方式1 - event.data: {raw_data[:200] if raw_data else 'None'}...")
                        
                        data = json.loads(raw_data)
                        inner_data = data.get('data', {})
                        event_type = inner_data.get('event', '')
                        
                        log(f"  事件类型: {event_type}")
                        log(f"  内部数据: {json.dumps(inner_data, ensure_ascii=False)[:300]}")
                        
                        if event_type == 'rewrite':
                            log(f"  ✅ 问题改写: {inner_data.get('rewrite', '')}")
                        elif event_type == 'selector':
                            log(f"  ✅ 选中表: {inner_data.get('selector', [])}")
                        elif event_type == 'sql':
                            sql_result = inner_data.get('sql', '')
                            log(f"  ✅ SQL: {sql_result}")
                            break
                        elif event_type == 'error':
                            error_result = inner_data.get('errorMsg', str(inner_data))
                            log(f"  ❌ 错误: {error_result}")
                            break
                    else:
                        log(f"  res 是 dict 但无 event: {list(res.keys()) if res else 'empty'}")
                
                # 方式2: 直接访问属性
                elif hasattr(res, 'data') and res.data:
                    log(f"  方式2 - res.data: {res.data[:200]}...")
                    data = json.loads(res.data)
                    event_type = data.get('event', data.get('data', {}).get('event', ''))
                    log(f"  事件类型: {event_type}")
                    
                    if 'sql' in str(data):
                        sql_result = data.get('sql') or data.get('data', {}).get('sql', '')
                        if sql_result:
                            log(f"  ✅ SQL: {sql_result}")
                            break
                else:
                    log(f"  ⚠️ 无法解析 res: {type(res)}, dir={dir(res)[:5]}")
                    if hasattr(res, '__dict__'):
                        log(f"  __dict__: {res.__dict__}")
                        
            except Exception as e:
                log(f"  解析异常: {e}")
            
            if event_count > 30:
                log("\n⚠️ 事件过多，停止")
                break
        
        log("\n" + "=" * 70)
        log("测试结果")
        log("=" * 70)
        log(f"总计收到: {event_count} 个事件")
        
        if sql_result:
            log(f"\n✅ 成功获取 SQL!")
            log(f"SQL:\n{sql_result}")
        elif error_result:
            log(f"\n❌ API 返回错误: {error_result}")
        else:
            log(f"\n⚠️ 未获取到 SQL")
            
    except Exception as e:
        log(f"\n❌ API 调用异常: {e}")
        import traceback
        log(traceback.format_exc())
    
    log(f"\n日志已保存到: {LOG_FILE}")
    return sql_result


if __name__ == "__main__":
    question = "查询库存数量小于100的产品"
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
    
    result = asyncio.run(test_xiyan_correct_api(question))
    
    if result:
        print(f"\n{'='*70}")
        print("✅ 测试成功！")
        print(f"{'='*70}")
