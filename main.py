from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
import json
import os
from datetime import datetime, timedelta
import astrbot.api.message_components as Comp
from astrbot.api.message_components import At

# 数据持久化，存 data 目录下
# AstrBot 推荐插件数据存储: data/plugins/data-invitecount/invite_data.json
# data 目录应取 context.data_dir 保证兼容任何主目录
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
INVITE_DATA_FILE = os.path.join(DATA_DIR, 'invite_data.json')

def load_data():
    if os.path.exists(INVITE_DATA_FILE):
        with open(INVITE_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(INVITE_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

def get_global_plugin_data_file(context):
    """
    返回主 data/plugin-data/invitecount.json 路径（与 plugins 并列，支持多环境）
    """
    # 推荐优先用 context.data_dir
    base = getattr(context, 'data_dir', None)
    if base and os.path.isdir(base):
        root = base
    else:
        # 向上递归找 'data' 目录
        now = os.path.abspath(os.path.dirname(__file__))
        while True:
            if os.path.basename(now) == 'data':
                root = now
                break
            parent = os.path.dirname(now)
            if parent == now:
                # 极端情况 fallback
                root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../data'))
                break
            now = parent
    plugin_data_dir = os.path.join(root, 'plugin-data')
    os.makedirs(plugin_data_dir, exist_ok=True)
    return os.path.join(plugin_data_dir, 'invitecount.json')

@register("invite_query", "bvzrays", "群邀请统计插件", "1.0.0")
class InviteQueryPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or AstrBotConfig({"only_stat_valid": False, "allow_at_query": True, "show_inviter": True})
        logger.info(f"[invite-plugin] 配置已注入/初始化: {dict(self.config or {})}")
        self.data_file = get_global_plugin_data_file(self.context)
        self.invite_data = self.load_data()

    async def initialize(self):
        logger.info(f"[invite] 配置已注入: {dict(self.config or {})}")

    def load_data(self):
        # 数据文件加载
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载邀请数据失败：{e}")
        return {}

    def save(self):
        try:
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(self.invite_data, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            logger.error(f"保存邀请数据失败：{e}")

    async def try_get_nickname(self, group_id, user_id):
        """优先查昵称，有接口用接口，无则直接ID"""
        try:
            if hasattr(self.context, "get_group_member_info"):
                member = await self.context.get_group_member_info(group_id, user_id)
                name = member.get("nickname") or member.get("card") or str(user_id)
                return name
        except Exception as e:
            logger.info(f"无法获取昵称: {e}")
        return str(user_id)

    async def sync_all_group_members(self, group_id):
        """同步并更新当前群所有入库QQ的nickname为最新群名片/昵称"""
        if not (group_id and hasattr(self.context, 'get_group_member_list')):
            return
        try:
            result = await self.context.get_group_member_list(group_id)
            updated = 0
            for member in result:
                user_id = str(member.get('user_id'))
                name = member.get('card') or member.get('nickname') or member.get('remark') or user_id
                if user_id in self.invite_data:
                    self.invite_data[user_id]['nickname'] = name
                    updated += 1
            if updated:
                self.save()
        except Exception as e:
            logger.info(f'[invite-debug] 同步群名片异常: {e}')

    async def safe_get_member_name_by_list(self, event, group_id, user_id):
        name = user_id
        try:
            # 只要 event 有 bot 和 api，就直接用
            if hasattr(event, "bot") and hasattr(event.bot, "api"):
                members = await event.bot.api.call_action('get_group_member_list', group_id=group_id)
                for member in members:
                    if str(member.get("user_id")) == str(user_id):
                        card = member.get("card", "").strip()
                        nickname = member.get("nickname", "").strip()
                        if card:
                            name = card
                        elif nickname:
                            name = nickname
                        break
        except Exception as e:
            logger.info(f'[invite debug] get_group_member_list失败: {e}')
        return name

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def handle_group_event(self, event: AstrMessageEvent):
        data = getattr(event.message_obj, 'raw_message', {})
        logger.info(f"[invite debug] 收到群事件: {data}")
        if not isinstance(data, dict):
            return
        post_type = data.get('post_type') or data.get('type')
        notice_type = data.get('notice_type') or data.get('event')
        group_id = str(data.get('group_id') or data.get('chat_id', ''))
        user_id = str(data.get('user_id') or data.get('target_id', ''))
        operator_id = str(data.get('operator_id') or data.get('inviter_id', ''))
        sub_type = data.get('sub_type')
        time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if post_type == "notice" and group_id:
            if notice_type == "group_increase":
                member_name = await self.try_get_nickname(group_id, user_id)
                if sub_type == "invite":
                    operator_name = await self.try_get_nickname(group_id, operator_id)
                    self.invite_data.setdefault(str(user_id), {})
                    self.invite_data[str(user_id)] = {
                        "nickname": member_name,
                        "inviter": str(operator_id),
                        "inviter_name": operator_name,
                        "join_type": "邀请",
                        "join_time": time,
                        "leave_type": None,
                        "leave_time": None
                    }
                    logger.info(f"[invite debug] 邀请入群已记: user_id={user_id}, inviter={operator_id}")
                    self.save()
                elif sub_type == "approve":
                    self.invite_data.setdefault(str(user_id), {})
                    self.invite_data[str(user_id)] = {
                        "nickname": member_name,
                        "inviter": None,
                        "inviter_name": None,
                        "join_type": "主动",
                        "join_time": time,
                        "leave_type": None,
                        "leave_time": None
                    }
                    logger.info(f"[invite debug] 主动加群已记: user_id={user_id}")
                    self.save()
            elif notice_type == "group_decrease":
                if sub_type == "leave":
                    if str(user_id) in self.invite_data:
                        self.invite_data[str(user_id)]["leave_type"] = "自己退群"
                        self.invite_data[str(user_id)]["leave_time"] = time
                        logger.info(f"[invite debug] 成员退群: user_id={user_id}")
                    self.save()
                elif sub_type == "kick":
                    if str(user_id) in self.invite_data:
                        self.invite_data[str(user_id)]["leave_type"] = f"被踢({operator_id})"
                        self.invite_data[str(user_id)]["leave_time"] = time
                        logger.info(f"[invite debug] 成员被踢: user_id={user_id}, by {operator_id}")
                    self.save()

    # ==== 查询统计命令 ====
    @filter.command("邀请查询")
    async def cmd_invite_query(self, event: AstrMessageEvent, qq: str = ""):
        user_id = None
        group_id = None
        if hasattr(event, 'get_group_id'):
            group_id = getattr(event, 'get_group_id', lambda: None)() or None
        if not group_id:
            raw = getattr(event.message_obj, 'raw_message', {})
            group_id = str(raw.get('group_id', None)) if raw else None
        await self.sync_all_group_members(group_id)
        from astrbot.api.message_components import At
        for seg in getattr(event.message_obj, "message", []):
            if isinstance(seg, At):
                user_id = str(seg.qq)
                break
            if getattr(seg, 'type', None) == 'at':
                data = getattr(seg, 'data', {})
                user_id = str(data.get('qq', '')) or str(getattr(seg, 'qq', ''))
                break
        if not user_id:
            arg_qq = qq.strip()
            if not arg_qq:
                args = (event.message_str or '').strip().split()
                if len(args) >= 2 and args[1].isdigit():
                    arg_qq = args[1]
            if arg_qq and arg_qq.isdigit():
                user_id = arg_qq
            else:
                user_id = event.get_sender_id()
        member = self.invite_data.get(str(user_id))
        created = False
        name = await self.safe_get_member_name_by_list(event, group_id, user_id)
        # fallback如有必要
        if not name or name == user_id:
            def getf(v):
                return v.strip() if isinstance(v,str) else v
            if group_id and hasattr(self.context, "get_group_member_info"):
                try:
                    result = await self.context.get_group_member_info(str(group_id), str(user_id))
                    logger.info(f'[invite debug] 单独接口查返回: {result}')
                    mi = result.get('data', result)
                    card = getf(mi.get('card', ''))
                    nickname = getf(mi.get('nickname', ''))
                    remark = getf(mi.get('remark', ''))
                    displayname = getf(mi.get('displayname', ''))
                    username = getf(mi.get('user_name', ''))
                    name = card or nickname or remark or displayname or username or user_id
                except Exception as e:
                    logger.info(f'[invite debug] get_group_member_info异常: {e}')
        if not member:
            member = {
                "nickname": name if name else user_id,
                "inviter": None,
                "inviter_name": None,
                "join_type": None,
                "join_time": None,
                "leave_type": None,
                "leave_time": None
            }
            self.invite_data[str(user_id)] = member
            self.save()
            created = True
        # 始终刷新本地nickname缓存
        self.invite_data[str(user_id)]["nickname"] = name if name else user_id
        self.save()
        inviter = member.get("inviter")
        inviter_name = None
        if self.config.get("show_inviter", True) and inviter:
            inviter_name = await self.safe_get_member_name_by_list(event, group_id, inviter)
        inviter_display = "自己/主动进群"
        if inviter and inviter_name:
            inviter_display = f"{inviter_name} ({inviter})"
        elif inviter:
            inviter_display = f"{inviter}"
        join_type = member.get("join_type") or "-"
        join_time = member.get("join_time") or "-"
        leave_type = member.get("leave_type")
        leave_time = member.get("leave_time")
        now = datetime.now()
        days_ago = "-"
        if join_time:
            try:
                join_dt = datetime.strptime(join_time, '%Y-%m-%d %H:%M:%S')
                days_ago = f"{(now - join_dt).days}天前({join_dt.strftime('%Y-%m-%d')})"
            except Exception:
                days_ago = join_time
        all_invited = [(u, v) for u, v in self.invite_data.items() if v.get("inviter") == str(user_id)]
        if self.config.get("only_stat_valid", False):
            invited = [item for item in all_invited if not item[1].get("leave_type")]
        else:
            invited = all_invited
        total_invite = len(all_invited)
        kicked = sum(1 for _,v in invited if v.get("leave_type") and "踢" in v.get("leave_type"))
        leave = sum(1 for _,v in invited if v.get("leave_type") == "自己退群")
        valid_invite = len([item for item in all_invited if not item[1].get("leave_type")])
        msg = f"====邀请统计====\n\n"
        msg += f"●被查用户：{name}\n"
        msg += f"●用户QQ：{user_id}\n"
        msg += f"●邀请人：{inviter_display}\n"
        msg += f"●进群方式：{join_type}\n"
        msg += f"●进群时间：{days_ago}\n"
        msg += f"●累计邀请：{total_invite} 人\n"
        msg += f"●被踢人数：{kicked} 人\n"
        msg += f"●自己退群：{leave} 人\n"
        msg += f"●有效邀请：{valid_invite} 人\n"
        msg += f"=================\n\n"
        msg += datetime.now().strftime('%Y/%m/%d %H:%M:%S')
        if created:
            msg = "【提示】该用户暂无数据，已帮你新建统计模板！\n" + msg
        yield event.plain_result(msg)

    @filter.command("我的邀请")
    async def cmd_my_invite(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        event.message_str = f"/邀请查询 @{user_id}"  # 伪造at查询自己
        async for r in self.cmd_invite_query(event):
            yield r

    @filter.command("邀请排行")
    async def cmd_invite_rank(self, event: AstrMessageEvent, mode: str = ""):
        """排行模式说明:
        /邀请排行         # 有效邀请数排行（有效 = 未退群/未被踢）
        /邀请排行 总      # 按总邀请数排行
        /邀请排行 差      # 按无效邀请数排行（被踢+自己退群之和）
        /邀请排行 帮助    # 展示用法说明
        """
        text = "====邀请排行====\n"
        args = (event.message_str or '').strip().split()
        if len(args) >= 2:
            mode = args[1].strip()
        if mode in {"help", "帮助", "h", "?"}:
            text += (
                "/邀请排行              —— 有效邀请排行\n"
                "/邀请排行 总（或 总人数）—— 总邀请排行\n"
                "/邀请排行 差（或 无效人数）—— 无效邀请排行（被踢+退群）\n"
                "/邀请排行 帮助         —— 显示本帮助\n"
            )
            yield event.plain_result(text)
            return

        # 汇总数据生成：所有邀请者ID，映射为 总/有效/无效数目
        count_map = {}   # inviter: [有效, 总, 无效]
        inviter_name_map = {}  # inviter: 昵称
        for v in self.invite_data.values():
            inviter = v.get("inviter")
            if not inviter: continue
            is_invalid = (v.get("leave_type") is not None)
            if inviter not in count_map:
                count_map[inviter] = [0,0,0] # 有效, 总, 无效
            count_map[inviter][1] += 1 # 总数
            if not is_invalid:
                count_map[inviter][0] += 1 # 有效
            else:
                count_map[inviter][2] += 1 # 无效
            # 邀请人昵称（如有）
            if inviter not in inviter_name_map or not inviter_name_map[inviter]:
                inviter_name_map[inviter] = self.invite_data.get(inviter, {}).get("nickname", inviter)

        # 排序模式
        display_mode = "有效邀请"
        if mode in {"总", "全部", "all", "人数", "总人数"}:
            sort_key = 1  # 总数
            display_mode = "总邀请"
        elif mode in {"差", "失效", "无效", "无效人数"}:
            sort_key = 2  # 无效
            display_mode = "无效邀请"
        else:
            sort_key = 0  # 有效
            display_mode = "有效邀请"

        sorted_list = sorted(count_map.items(), key=lambda x: -x[1][sort_key])
        text += f"({display_mode}排行，前10)\n"
        for idx, (uid, tpl) in enumerate(sorted_list[:10], 1):
            name = inviter_name_map.get(uid, uid)
            text += (
                f"{idx}. {name}({uid}) | 有效:{tpl[0]} 总:{tpl[1]} 无效:{tpl[2]}\n"
            )
        if not sorted_list:
            text += "无邀请记录\n"
        yield event.plain_result(text)

    @filter.command("邀请奖励")
    async def cmd_invite_reward(self, event: AstrMessageEvent):
        msg = self.config.get("reward_message", "暂无奖励内容\n请联系管理员在WebUI配置奖励说明")
        yield event.plain_result(msg)

    async def terminate(self):
        # 卸载插件时可扩展资源释放逻辑
        pass
