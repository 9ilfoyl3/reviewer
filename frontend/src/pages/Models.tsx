/**
 * Models —— 模型配置页面（挪用 artoo 的卡片网格 + 新增弹窗 + 空状态设计）。
 *
 * 相比 artoo 精简了配置项：仅保留 名称 / Base URL / 模型 / API Key / 是否默认，
 * 并提供连通性测试。Worker 优先使用「默认」配置驱动推理，缺省回退环境变量。
 *
 * 数据流向清晰：页面持有配置列表与弹窗表单态，操作后重新拉取列表刷新。
 */

import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import {
  CheckCircle,
  Cpu,
  Globe,
  Loader2,
  Pencil,
  Plus,
  Star,
  Trash2,
  XCircle,
  Zap,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { EmptyState } from '@/components/EmptyState'
import {
  ApiError,
  createModelConfig,
  deleteModelConfig,
  fetchModelConfigs,
  testModelConfig,
  updateModelConfig,
  type ModelConfigItem,
  type ModelConfigInput,
} from '@/lib/api'
import { cn } from '@/lib/utils'

interface FormState extends ModelConfigInput {
  api_key: string
}

const EMPTY_FORM: FormState = {
  name: '',
  base_url: '',
  model: '',
  api_key: '',
  is_default: false,
}

export function Models() {
  const [configs, setConfigs] = useState<ModelConfigItem[]>([])
  const [loading, setLoading] = useState(true)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [showDialog, setShowDialog] = useState(false)
  const [dialogTestResult, setDialogTestResult] = useState<{
    success: boolean
    message: string
    reply?: string | null
  } | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setConfigs(await fetchModelConfigs())
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : '加载模型配置失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const openCreate = () => {
    setForm(EMPTY_FORM)
    setEditingId(null)
    setDialogTestResult(null)
    setShowDialog(true)
  }

  const openEdit = (c: ModelConfigItem) => {
    setForm({
      name: c.name,
      base_url: c.base_url,
      model: c.model,
      api_key: '',
      is_default: c.is_default,
    })
    setEditingId(c.id)
    setDialogTestResult(null)
    setShowDialog(true)
  }

  const closeDialog = () => {
    setShowDialog(false)
    setEditingId(null)
  }

  const validate = (): string | null => {
    if (!form.name.trim()) return '请填写配置名称'
    if (!form.base_url.trim()) return '请填写 Base URL'
    if (!form.model.trim()) return '请填写模型名称'
    return null
  }

  const handleSave = async () => {
    const err = validate()
    if (err) {
      toast.error(err)
      return
    }
    setSaving(true)
    try {
      const payload: ModelConfigInput = {
        name: form.name.trim(),
        base_url: form.base_url.trim(),
        model: form.model.trim(),
        is_default: form.is_default,
      }
      // API Key 留空且为编辑时不覆盖已存密钥。
      if (form.api_key.trim()) payload.api_key = form.api_key.trim()

      if (editingId) {
        await updateModelConfig(editingId, payload)
        toast.success('模型配置已更新')
      } else {
        await createModelConfig(payload)
        toast.success('模型配置已创建')
      }
      closeDialog()
      await load()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    if (!form.base_url.trim() || !form.model.trim()) {
      toast.error('请先填写 Base URL 与模型名称')
      return
    }
    setTesting(true)
    setDialogTestResult(null)
    try {
      const result = await testModelConfig({
        base_url: form.base_url.trim(),
        model: form.model.trim(),
        api_key: form.api_key.trim() || undefined,
        config_id: editingId ?? undefined,
      })
      setDialogTestResult(result)
    } catch (e) {
      setDialogTestResult({
        success: false,
        message: e instanceof ApiError ? e.message : '测试失败',
      })
    } finally {
      setTesting(false)
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await deleteModelConfig(id)
      toast.success('已删除')
      await load()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '删除失败')
    }
  }

  return (
    <div className="p-6">
      {/* 页面头部 */}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">模型配置</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            配置体检使用的 LLM 模型。设为默认的模型将用于所有体检；未配置时回退到环境变量。
          </p>
        </div>
        <Button onClick={openCreate} className="gap-2">
          <Plus className="size-4" />
          新增模型
        </Button>
      </div>

      {/* 列表 / 空状态 */}
      {loading ? (
        <div className="flex items-center justify-center gap-2 py-20 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          加载中…
        </div>
      ) : configs.length === 0 ? (
        <EmptyState
          icon={Cpu}
          title="还没有模型配置"
          description="新增一个，或继续使用环境变量中的默认模型。"
          action={
            <Button onClick={openCreate} variant="outline" className="gap-2">
              <Plus className="size-4" />
              新增模型
            </Button>
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {configs.map((c) => (
            <div
              key={c.id}
              className="group relative rounded-xl border border-border bg-card p-5 transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/20 hover:shadow-lg"
            >
              {c.is_default ? (
                <div className="absolute right-4 top-4">
                  <Star className="size-4 fill-yellow-500 text-yellow-500" />
                </div>
              ) : null}
              <div className="mb-3 flex size-10 items-center justify-center rounded-lg bg-primary/10">
                <Globe className="size-5 text-primary" />
              </div>
              <div className="mb-3 flex items-center gap-2">
                <h3 className="truncate text-base font-semibold">{c.name}</h3>
                {c.api_key_set ? (
                  <Badge
                    variant="outline"
                    className="shrink-0 border-primary/20 bg-primary/5 text-xs text-primary"
                  >
                    已设密钥
                  </Badge>
                ) : null}
              </div>
              <div className="mb-4 space-y-1.5 text-sm text-muted-foreground">
                <p className="truncate">
                  <span className="text-foreground/60">地址:</span> {c.base_url}
                </p>
                <p className="truncate">
                  <span className="text-foreground/60">模型:</span> {c.model}
                </p>
                <p>
                  <span className="text-foreground/60">密钥:</span>{' '}
                  {c.api_key_set ? '已设置' : '未设置'}
                </p>
              </div>
              <div className="flex items-center gap-1 border-t border-border/60 pt-3">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 gap-1 text-xs"
                  onClick={() => openEdit(c)}
                >
                  <Pencil className="size-3.5" />
                  编辑
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 gap-1 text-xs text-destructive hover:text-destructive"
                  onClick={() => handleDelete(c.id)}
                >
                  <Trash2 className="size-3.5" />
                  删除
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 新增 / 编辑弹窗（挪用 artoo dialog） */}
      <Dialog open={showDialog} onOpenChange={(o) => (o ? undefined : closeDialog())}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editingId ? '编辑模型' : '新增模型'}</DialogTitle>
          </DialogHeader>
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="mc-name">配置名称</Label>
              <Input
                id="mc-name"
                placeholder="例如 DeepSeek V3"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="mc-url">Base URL</Label>
              <Input
                id="mc-url"
                placeholder="https://api.deepseek.com/v1"
                value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.target.value })}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="mc-model">模型名称</Label>
              <Input
                id="mc-model"
                placeholder="deepseek-chat"
                value={form.model}
                onChange={(e) => setForm({ ...form, model: e.target.value })}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="mc-key">API Key</Label>
              <Input
                id="mc-key"
                type="password"
                autoComplete="off"
                placeholder={editingId ? '留空则不修改已保存的密钥' : 'sk-...'}
                value={form.api_key}
                onChange={(e) => setForm({ ...form, api_key: e.target.value })}
              />
            </div>
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4 rounded border-border accent-primary"
                checked={form.is_default}
                onChange={(e) => setForm({ ...form, is_default: e.target.checked })}
              />
              设为默认模型（用于所有体检）
            </label>

            {dialogTestResult ? (
              <div
                className={cn(
                  'flex items-center gap-1.5 rounded-lg border p-2.5 text-xs',
                  dialogTestResult.success
                    ? 'border-primary/20 bg-primary/5 text-primary'
                    : 'border-destructive/20 bg-destructive/5 text-destructive',
                )}
              >
                {dialogTestResult.success ? (
                  <CheckCircle className="size-3.5" />
                ) : (
                  <XCircle className="size-3.5" />
                )}
                <span>{dialogTestResult.message}</span>
              </div>
            ) : null}
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={handleTest}
              disabled={testing || !form.base_url || !form.model}
              className="gap-1.5"
            >
              {testing ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Zap className="size-4" />
              )}
              测试连接
            </Button>
            <Button type="button" variant="outline" onClick={closeDialog}>
              取消
            </Button>
            <Button type="button" onClick={handleSave} disabled={saving} className="gap-1.5">
              {saving ? <Loader2 className="size-4 animate-spin" /> : null}
              {editingId ? '保存' : '创建'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
