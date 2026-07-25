import { ref } from 'vue'
import { defineStore } from 'pinia'
import { mapApiError } from '@/api/client'
import { tagApi } from '@/api/tags'
import type { ManagedTag, TagCreate, TagUpdate } from '@/api/types'

export const useTagStore = defineStore('tags', () => {
  const items = ref<ManagedTag[]>([])
  const loading = ref(false)
  const errorMessage = ref('')

  async function load(q?: string) {
    loading.value = true
    errorMessage.value = ''
    try {
      items.value = await tagApi.list(q)
    } catch (error) {
      errorMessage.value = mapApiError(error).message
    } finally {
      loading.value = false
    }
  }

  async function create(input: TagCreate) {
    const created = await tagApi.create(input)
    items.value.push(created)
    return created
  }

  async function update(id: string, input: TagUpdate) {
    const updated = await tagApi.update(id, input)
    const index = items.value.findIndex((item) => item.id === id)
    if (index >= 0) items.value[index] = updated
    return updated
  }

  async function remove(item: ManagedTag, force = false) {
    await tagApi.remove(item.id, item.version, force)
    items.value = items.value.filter((entry) => entry.id !== item.id)
  }

  return { items, loading, errorMessage, load, create, update, remove }
})
