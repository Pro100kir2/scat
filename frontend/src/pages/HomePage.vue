<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import HeroSection from '@/components/hero/HeroSection.vue'
import ResultsSection from '@/components/results/ResultsSection.vue'
import SubscriptionSection from '@/components/sections/SubscriptionSection.vue'
import HowItWorks from '@/components/sections/HowItWorks.vue'
import FaqSection from '@/components/sections/FaqSection.vue'
import { useScan } from '@/composables/useScan'

const scan = useScan()
onMounted(scan.attach)
onBeforeUnmount(scan.detach)
</script>

<template>
  <main>
    <HeroSection />
    <ResultsSection />
    <SubscriptionSection />
    <HowItWorks />
    <FaqSection />
  </main>
</template>
