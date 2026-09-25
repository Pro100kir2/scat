<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import SiteHeader from '@/components/layout/SiteHeader.vue'
import SiteFooter from '@/components/layout/SiteFooter.vue'
import HeroSection from '@/components/hero/HeroSection.vue'
import ResultsSection from '@/components/results/ResultsSection.vue'
import SubscriptionSection from '@/components/sections/SubscriptionSection.vue'
import HowItWorks from '@/components/sections/HowItWorks.vue'
import FaqSection from '@/components/sections/FaqSection.vue'
import WelcomeDialog from '@/components/ui/WelcomeDialog.vue'
import { useScan } from '@/composables/useScan'

const scan = useScan()
onMounted(scan.attach)
onBeforeUnmount(scan.detach)
</script>

<template>
  <SiteHeader />
  <main>
    <HeroSection />
    <ResultsSection />
    <SubscriptionSection />
    <HowItWorks />
    <FaqSection />
  </main>
  <SiteFooter />
  <WelcomeDialog />
</template>
