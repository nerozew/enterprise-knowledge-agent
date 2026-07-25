<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const formRef = ref<FormInstance>()
const submitting = ref(false)
const form = reactive({ display_name: '', email: '', password: '', confirmPassword: '' })
const rules: FormRules = {
  display_name: [
    { required: true, whitespace: true, message: '请输入昵称', trigger: 'blur' },
    { max: 80, message: '昵称不能超过 80 个字符', trigger: 'change' },
  ],
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '请输入有效的邮箱地址', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, message: '密码至少 8 个字符', trigger: 'blur' },
  ],
  confirmPassword: [
    {
      validator: (_rule: unknown, value: string, callback: (error?: Error) => void) => {
        if (!value) callback(new Error('请再次输入密码'))
        else if (value !== form.password) callback(new Error('两次输入的密码不一致'))
        else callback()
      },
      trigger: 'blur',
    },
  ],
}

async function submit() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  submitting.value = true
  try {
    await auth.register({ display_name: form.display_name.trim(), email: form.email, password: form.password })
    await router.replace('/workspace')
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
      <p class="eyebrow">建立你的灵感资料库</p>
      <h1>从今天的一句话开始。</h1>
      <p>你的记录默认与其他用户隔离，只有登录后才能访问。</p>
    </section>
    <section class="auth-card" aria-labelledby="register-title">
      <p class="eyebrow">创建账户</p>
      <h2 id="register-title">开启灵感空间</h2>
      <el-alert v-if="auth.errorMessage" type="error" :title="auth.errorMessage" show-icon :closable="false" />
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
        <el-form-item label="昵称" prop="display_name">
          <el-input v-model="form.display_name" maxlength="80" autocomplete="name" />
        </el-form-item>
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="form.email" type="email" autocomplete="email" placeholder="you@example.com" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input v-model="form.password" type="password" show-password autocomplete="new-password" />
        </el-form-item>
        <el-form-item label="确认密码" prop="confirmPassword">
          <el-input v-model="form.confirmPassword" type="password" show-password autocomplete="new-password" />
        </el-form-item>
        <el-button type="primary" native-type="submit" :loading="submitting" class="full-width">注册并进入</el-button>
      </el-form>
      <p class="auth-switch">已有账户？<router-link to="/login">返回登录</router-link></p>
    </section>
  </main>
</template>
