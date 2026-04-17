/** 空会话时展示的快捷问题（游戏社交平台 · 普通玩家向智能客服） */
export type SuggestedPrompt = {
  id: string
  /** 按钮上显示的短文案 */
  label: string
  /** 填入输入框的完整问题 */
  text: string
}

export const SUGGESTED_PROMPTS: SuggestedPrompt[] = [
  {
    id: 'flash_sale',
    label: '限时抢购规则',
    text: '平台的限时抢购/闪购活动一般几点开抢？如果没抢到会自动退款吗？有没有防脚本或公平性说明？',
  },
  {
    id: 'order_issue',
    label: '抢购订单异常',
    text: '我抢购下单时提示支付成功但订单显示待确认，大概多久会更新？若超时未发货该怎么联系处理？',
  },
  {
    id: 'post_moderation',
    label: '发帖与审核',
    text: '发动态或攻略帖一直被审核中，常见原因有哪些？哪些内容容易被屏蔽，申诉入口在哪里？',
  },
  {
    id: 'social_features',
    label: '好友与社交',
    text: '怎么添加游戏好友、关注博主或加入兴趣圈子？陌生人私信和组队邀请可以怎么设置权限？',
  },
  {
    id: 'account_wallet',
    label: '充值与道具',
    text: '充值点券或购买外观后长时间未到账，需要提供哪些截图找客服？退款或误购一般怎么处理？',
  },
  {
    id: 'report_safety',
    label: '举报与安全',
    text: '遇到外挂、诈骗链接或聊天辱骂，在哪里一键举报？大概多久会反馈处理结果？',
  },
]
