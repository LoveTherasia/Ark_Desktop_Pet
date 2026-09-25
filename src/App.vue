<script setup lang="ts">
import { ref } from 'vue'
import AmiyaModel from './components/AmiyaModel.vue'

const interactionCount = ref(0)
const mood = ref('待机中')

function interact() {
  interactionCount.value += 1
  mood.value = interactionCount.value % 2 === 0 ? '心情不错' : '被摸到了'
}

void interact
</script>

<template>
  <main class="pet-shell">
    <section class="pet-card" aria-label="明日方舟桌宠">
      <header class="pet-header">
        <span class="status-dot" aria-hidden="true"></span>
        <span>ARK PET</span>
      </header>

      <button class="pet-stage" type="button" aria-label="和阿米娅互动" @click="interact">
        <AmiyaModel :interaction-key="interactionCount" />
      </button>

      <div class="pet-info">
        <div>
          <p class="pet-label">当前状态</p>
          <strong>{{ mood }}</strong>
        </div>
        <span class="interaction-count">互动 {{ interactionCount }}</span>
      </div>
    </section>
  </main>
</template>
