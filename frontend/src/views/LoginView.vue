<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const formRef = ref<FormInstance>()
const submitting = ref(false)
const form = reactive({ email: '', password: '' })
const rules: FormRules = {
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '请输入有效的邮箱地址', trigger: 'blur' },
  ],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function submit() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  submitting.value = true
  try {
    await auth.login(form)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/workspace'
    await router.replace(redirect)
  } catch {
    // Store exposes a safe, user-facing message.
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="auth-page">
    <section class="auth-intro">
      <span class="brand-mark large" aria-hidden="true">✦</span>
      <p class="eyebrow">灵感拾光</p>
      <h1>给每一个突然出现的想法，留一盏灯。</h1>
      <p>快速记下、轻松整理，再用 AI 帮你看见更多可能。</p>
    </section>
    <section class="auth-card" aria-labelledby="login-title">
      <p class="eyebrow">欢迎回来</p>
      <h2 id="login-title">登录你的灵感空间</h2>
      <el-alert v-if="auth.errorMessage" type="error" :title="auth.errorMessage" show-icon :closable="false" />
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="form.email" type="email" autocomplete="email" placeholder="you@example.com" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input v-model="form.password" type="password" show-password autocomplete="current-password" />
        </el-form-item>
        <el-button type="primary" native-type="submit" :loading="submitting" class="full-width">登录</el-button>
      </el-form>
      <p class="auth-switch">还没有账户？<router-link to="/register">立即注册</router-link></p>
    </section>
  </main>
</template>
