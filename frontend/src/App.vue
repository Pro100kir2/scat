<script setup lang="ts">
import { useRoute } from 'vue-router'
import SiteHeader from '@/components/layout/SiteHeader.vue'
import SiteFooter from '@/components/layout/SiteFooter.vue'
import WelcomeDialog from '@/components/ui/WelcomeDialog.vue'

const route = useRoute()
</script>

<template>
  <RouterView v-if="route.meta.admin" />
  <template v-else>
    <SiteHeader />
    <RouterView />
    <SiteFooter />
    <WelcomeDialog />
  </template>
</template>
