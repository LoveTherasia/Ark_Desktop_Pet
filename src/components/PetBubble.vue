<script setup lang="ts">
/**
 * 桌宠旁边的聊天气泡。
 * 只负责渲染一条通用消息，不关心消息来源——活动检测只是当前唯一的生产者，
 * 后续功能（提醒、对话、事件）可以直接复用同一个消息结构。
 */
export type BubbleMessage = {
  /** 生产者标识，例如 activity / reminder / chat */
  source: string
  /** 图标（emoji） */
  icon: string
  /** 主标题，例如“浏览器” */
  title: string
  /** 次要说明，例如进程名 chrome */
  detail?: string
}

defineProps<{ message: BubbleMessage }>()
</script>

<template>
  <div class="pet-bubble" role="status">
    <span class="pet-bubble-icon" aria-hidden="true">{{ message.icon }}</span>
    <div class="pet-bubble-body">
      <p class="pet-bubble-title">{{ message.title }}</p>
      <p v-if="message.detail" class="pet-bubble-detail">{{ message.detail }}</p>
    </div>
  </div>
</template>
