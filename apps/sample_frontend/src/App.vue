<template>
  <div class="app-shell">
    <div class="bg-orb orb-1" aria-hidden="true"></div>
    <div class="bg-orb orb-2" aria-hidden="true"></div>
    <div class="bg-orb orb-3" aria-hidden="true"></div>
    <RouterView />
  </div>
</template>

<script setup>
import { RouterView } from "vue-router";
import { onMounted } from "vue";
import { authState, fetchMe } from "./lib/auth";

onMounted(async () => {
  if (authState.token && !authState.checked) {
    await fetchMe();
  }
});
</script>
