<script setup lang="ts">
import SiteHeader from '@/components/layout/SiteHeader.vue'
import SiteFooter from '@/components/layout/SiteFooter.vue'
import WelcomeDialog from '@/components/ui/WelcomeDialog.vue'
</script>

<template>
  <SiteHeader />
  <RouterView />
  <SiteFooter />
  <WelcomeDialog />
</template>
