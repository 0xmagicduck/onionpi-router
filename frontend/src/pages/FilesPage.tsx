import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronRight,
  ChevronsUpDown,
  Download,
  File,
  FileArchive,
  FileImage,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  Link2,
  Search,
  Trash2,
  UploadCloud,
  X,
} from 'lucide-react'
import { ChangeEvent, DragEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, uploadFile } from '../api'
import { ActivityPanel } from '../components/ActivityPanel'
import { ChatPanel } from '../components/ChatPanel'
import { ConfirmDialog, Modal } from '../components/Modal'
import { Panel } from '../components/Panel'
import { EmptyState } from '../components/ui'
import { formatBytes, relativeTime } from '../lib'
import type { Activity, ChatMessage, FilesPayload, SharedFile } from '../types'

type Props = {
  activities: Activity[]
  chat: {
    messages: ChatMessage[]
    online: number
    connected: boolean
    send: (body: string) => boolean
  }
  notify: (message: string, error?: boolean) => void
}

type SortKey = 'name' | 'size' | 'modified'

function FileIcon({ item }: { item: SharedFile }) {
  if (item.is_directory) return <Folder className="file-folder" />
  if (item.mime.startsWith('image/')) return <FileImage className="file-image" />
  if (item.mime.includes('zip')) return <FileArchive className="file-archive" />
  if (item.mime.includes('pdf') || item.mime.startsWith('text/')) return <FileText className="file-document" />
  return <File />
}

function downloadUrl(path: string): string {
  return `/api/v1/files/download?path=${encodeURIComponent(path)}`
}

export function FilesPage({ activities, chat, notify }: Props) {
  const [currentPath, setCurrentPath] = useState('')
  const [payload, setPayload] = useState<FilesPayload>()
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: 'name', desc: false })
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [uploads, setUploads] = useState<Array<{ name: string; progress: number }>>([])
  const [dragging, setDragging] = useState(false)
  const [folderDialog, setFolderDialog] = useState(false)
  const [folderName, setFolderName] = useState('')
  const [pendingDelete, setPendingDelete] = useState<SharedFile[]>()
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const refresh = useCallback(async () => {
    try {
      setPayload(await api.files(currentPath))
      setError('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Fichiers indisponibles')
    }
  }, [currentPath])

  useEffect(() => {
    void refresh()
    setSelected(new Set())
  }, [refresh])

  const visible = useMemo(() => {
    const query = search.trim().toLocaleLowerCase('fr')
    const items = (payload?.items ?? []).filter((item) => item.name.toLocaleLowerCase('fr').includes(query))
    return items.sort((left, right) => {
      // Folders first, whatever the sort: it is how every file manager behaves.
      if (left.is_directory !== right.is_directory) return left.is_directory ? -1 : 1
      const direction = sort.desc ? -1 : 1
      if (sort.key === 'size') return direction * (left.size - right.size)
      if (sort.key === 'modified') return direction * (left.modified_at - right.modified_at)
      return direction * left.name.localeCompare(right.name, 'fr')
    })
  }, [payload, search, sort])

  const uploadOne = async (file: globalThis.File) => {
    setUploads((current) => [...current, { name: file.name, progress: 0 }])
    const update = (progress: number) =>
      setUploads((current) => current.map((item) => (item.name === file.name ? { ...item, progress } : item)))
    try {
      await uploadFile(file, currentPath, update)
      notify(`${file.name} importé`)
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Échec de l’importation'
      setError(message)
      notify(message, true)
    } finally {
      setUploads((current) => current.filter((item) => item.name !== file.name))
    }
  }

  /** Uploads run one after another: the Pi has a single slow disk. */
  const processFiles = async (files: FileList | globalThis.File[]) => {
    setError('')
    for (const file of Array.from(files)) await uploadOne(file)
    await refresh()
  }

  const fileChosen = (event: ChangeEvent<HTMLInputElement>) => {
    if (event.target.files?.length) void processFiles(event.target.files)
    event.target.value = ''
  }

  const drop = (event: DragEvent) => {
    event.preventDefault()
    setDragging(false)
    if (event.dataTransfer.files.length) void processFiles(event.dataTransfer.files)
  }

  const createFolder = async () => {
    try {
      await api.createFolder(currentPath, folderName)
      setFolderDialog(false)
      setFolderName('')
      notify('Dossier créé')
      await refresh()
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Création impossible'
      setError(message)
      notify(message, true)
    }
  }

  const confirmDelete = async () => {
    if (!pendingDelete?.length) return
    setDeleting(true)
    let failures = 0
    for (const item of pendingDelete) {
      try {
        await api.deleteFile(item.path)
      } catch {
        failures += 1
      }
    }
    setDeleting(false)
    setPendingDelete(undefined)
    setSelected(new Set())
    notify(
      failures
        ? `${failures} élément(s) n’ont pas pu être supprimés`
        : `${pendingDelete.length} élément${pendingDelete.length > 1 ? 's supprimés' : ' supprimé'}`,
      failures > 0,
    )
    await refresh()
  }

  const copyLink = async (item: SharedFile) => {
    try {
      await navigator.clipboard?.writeText(new URL(downloadUrl(item.path), window.location.origin).toString())
      notify('Lien de téléchargement copié')
    } catch {
      notify('Le presse-papiers n’est pas accessible', true)
    }
  }

  /** The API downloads one path at a time, so a selection becomes a queue of
   *  anchor clicks rather than a single archive. */
  const downloadSelection = () => {
    const files = visible.filter((item) => selected.has(item.path) && !item.is_directory)
    if (!files.length) {
      notify('Sélectionnez au moins un fichier (les dossiers ne se téléchargent pas)', true)
      return
    }
    files.forEach((item, index) => {
      window.setTimeout(() => {
        const link = document.createElement('a')
        link.href = downloadUrl(item.path)
        link.download = item.name
        link.click()
      }, index * 350)
    })
    notify(`${files.length} téléchargement${files.length > 1 ? 's' : ''} lancé${files.length > 1 ? 's' : ''}`)
  }

  const toggle = (path: string) =>
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })

  const toggleSort = (key: SortKey) =>
    setSort((current) => (current.key === key ? { key, desc: !current.desc } : { key, desc: key !== 'name' }))

  const header = (key: SortKey, label: string) => {
    const state = sort.key === key ? (sort.desc ? 'descending' : 'ascending') : 'none'
    const Icon = sort.key !== key ? ChevronsUpDown : sort.desc ? ArrowDown : ArrowUp
    return (
      <button className="column-sort" aria-sort={state} onClick={() => toggleSort(key)}>{label}<Icon size={12} /></button>
    )
  }

  const used = payload?.storage.used ?? 0
  const total = payload?.storage.total ?? 0
  const percent = total ? Math.round((used / total) * 100) : 0
  const breadcrumbs = currentPath ? currentPath.split('/') : []
  const allSelected = visible.length > 0 && visible.every((item) => selected.has(item.path))

  return (
    <div className="page files-page">
      <div className="files-layout">
        <main className="files-main">
          <div className="page-title title-with-action">
            <div>
              <h1>Fichiers partagés</h1>
              <p>Un espace privé, disponible uniquement sur votre réseau local.</p>
            </div>
            <div className="title-actions">
              <button className="button button-secondary" onClick={() => setFolderDialog(true)}>
                <FolderPlus size={16} />Nouveau dossier
              </button>
              <button className="button button-primary" onClick={() => inputRef.current?.click()}>
                <UploadCloud size={16} />Importer
              </button>
              <input ref={inputRef} className="sr-only" type="file" multiple onChange={fileChosen} />
            </div>
          </div>

          <nav className="breadcrumbs" aria-label="Fil d’Ariane">
            <button onClick={() => setCurrentPath('')}>Accueil</button>
            {breadcrumbs.map((part, index) => (
              <span key={`${part}-${index}`}>
                <ChevronRight size={14} />
                <button onClick={() => setCurrentPath(breadcrumbs.slice(0, index + 1).join('/'))}>{part}</button>
              </span>
            ))}
          </nav>

          {error && (
            <p className="inline-error" role="alert">
              <X size={16} />{error}
              <button onClick={() => setError('')} aria-label="Fermer"><X size={15} /></button>
            </p>
          )}

          <section className="panel storage-panel">
            <div className="storage-head">
              <strong>{formatBytes(used)} <span>utilisés sur {formatBytes(total)}</span></strong>
              <span className="muted">{percent} %</span>
            </div>
            <div
              className={`meter-track ${percent >= 90 ? 'meter-danger' : percent >= 75 ? 'meter-warn' : ''}`}
              role="meter"
              aria-label="Stockage utilisé"
              aria-valuenow={percent}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <span style={{ width: `${percent}%` }} />
            </div>
          </section>

          <Panel
            className="file-browser"
            title="Contenu du dossier"
            subtitle={`${visible.length} élément${visible.length > 1 ? 's' : ''}`}
            action={
              <label className="search-field">
                <Search size={16} />
                <span className="sr-only">Rechercher un fichier</span>
                <input
                  type="search"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Rechercher dans ce dossier"
                />
              </label>
            }
          >
            {selected.size > 0 && (
              <div className="bulk-bar">
                <strong>{selected.size} sélectionné{selected.size > 1 ? 's' : ''}</strong>
                <button className="button button-small button-secondary" onClick={downloadSelection}>
                  <Download size={14} /> Télécharger
                </button>
                <button
                  className="button button-small button-danger"
                  onClick={() => setPendingDelete(visible.filter((item) => selected.has(item.path)))}
                >
                  <Trash2 size={14} /> Supprimer
                </button>
                <button className="button button-small button-ghost" onClick={() => setSelected(new Set())}>
                  Annuler la sélection
                </button>
              </div>
            )}

            <div
              className={`drop-zone ${dragging ? 'drop-zone-active' : ''}`}
              onDragOver={(event) => { event.preventDefault(); setDragging(true) }}
              onDragLeave={() => setDragging(false)}
              onDrop={drop}
              onClick={() => inputRef.current?.click()}
            >
              <UploadCloud size={26} />
              <span>Glissez-déposez vos fichiers ici, ou cliquez pour les choisir</span>
            </div>

            {uploads.map((upload) => (
              <div className="upload-row" key={upload.name}>
                <File size={18} />
                <strong title={upload.name}>{upload.name}</strong>
                <div className="meter-track"><span style={{ width: `${upload.progress}%` }} /></div>
                <span>{upload.progress} %</span>
              </div>
            ))}

            <div className="data-table file-table" role="table">
              <div className="table-row table-head" role="row">
                <span role="columnheader">
                  <label className="select-box">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={() => setSelected(allSelected ? new Set() : new Set(visible.map((item) => item.path)))}
                    />
                    <span><Check size={13} /></span>
                    <span className="sr-only">Tout sélectionner</span>
                  </label>
                </span>
                <span role="columnheader">{header('name', 'Nom')}</span>
                <span role="columnheader">Type</span>
                <span role="columnheader">{header('size', 'Taille')}</span>
                <span role="columnheader">{header('modified', 'Modifié')}</span>
                <span role="columnheader"><span className="sr-only">Actions</span></span>
              </div>

              {visible.map((item) => (
                <div
                  className={`table-row table-row-interactive ${selected.has(item.path) ? 'file-selected' : ''}`}
                  role="row"
                  key={item.path}
                >
                  <span role="cell">
                    <label className="select-box">
                      <input type="checkbox" checked={selected.has(item.path)} onChange={() => toggle(item.path)} />
                      <span><Check size={13} /></span>
                      <span className="sr-only">Sélectionner {item.name}</span>
                    </label>
                  </span>
                  <span role="cell">
                    {item.is_directory ? (
                      <button className="file-name" onClick={() => setCurrentPath(item.path)}>
                        <FileIcon item={item} /><strong title={item.name}>{item.name}</strong>
                      </button>
                    ) : (
                      <a className="file-name" href={downloadUrl(item.path)}>
                        <FileIcon item={item} /><strong title={item.name}>{item.name}</strong>
                      </a>
                    )}
                  </span>
                  <span role="cell">{item.is_directory ? 'Dossier' : item.mime.split('/')[1]?.toUpperCase() || 'Fichier'}</span>
                  <span className="tabular" role="cell">{item.is_directory ? '—' : formatBytes(item.size)}</span>
                  <span role="cell">{relativeTime(item.modified_at)}</span>
                  <span className="file-actions" role="cell">
                    {!item.is_directory && (
                      <>
                        <a href={downloadUrl(item.path)} className="icon-button" title="Télécharger" download>
                          <Download size={16} />
                        </a>
                        <button className="icon-button" title="Copier le lien" onClick={() => void copyLink(item)}>
                          <Link2 size={16} />
                        </button>
                      </>
                    )}
                    <button
                      className="icon-button danger-hover"
                      title={`Supprimer ${item.name}`}
                      onClick={() => setPendingDelete([item])}
                    >
                      <Trash2 size={16} />
                    </button>
                  </span>
                </div>
              ))}

              {!visible.length && (
                <EmptyState
                  icon={FolderOpen}
                  title={search ? 'Aucun fichier ne correspond' : 'Ce dossier est vide'}
                  action={
                    !search ? (
                      <button className="button button-secondary" onClick={() => inputRef.current?.click()}>
                        <UploadCloud size={16} /> Importer un premier fichier
                      </button>
                    ) : undefined
                  }
                >
                  {search
                    ? 'Essayez un autre terme, ou remontez d’un niveau dans l’arborescence.'
                    : 'Les fichiers déposés ici restent sur la Raspberry Pi et ne sortent jamais du réseau local.'}
                </EmptyState>
              )}

              {visible.length > 0 && (
                <div className="file-foot">
                  <span>{visible.length} élément{visible.length > 1 ? 's' : ''}</span>
                  <span>{selected.size} sélectionné{selected.size > 1 ? 's' : ''}</span>
                </div>
              )}
            </div>
          </Panel>
        </main>

        <aside className="files-rail">
          <ActivityPanel activities={activities.slice(0, 4)} />
          <ChatPanel {...chat} compact />
        </aside>
      </div>

      {folderDialog && (
        <Modal
          title="Nouveau dossier"
          description={`Il sera créé dans ${currentPath || 'Accueil'}.`}
          icon={<FolderPlus size={22} />}
          onClose={() => setFolderDialog(false)}
          onSubmit={(event) => { event.preventDefault(); void createFolder() }}
          actions={
            <>
              <button type="button" className="button button-secondary" onClick={() => setFolderDialog(false)}>Annuler</button>
              <button className="button button-primary" disabled={!folderName.trim()}>Créer</button>
            </>
          }
        >
          <label>
            Nom du dossier
            <input value={folderName} onChange={(event) => setFolderName(event.target.value)} required maxLength={120} />
          </label>
        </Modal>
      )}

      {pendingDelete && (
        <ConfirmDialog
          title={pendingDelete.length > 1 ? `Supprimer ${pendingDelete.length} éléments ?` : `Supprimer « ${pendingDelete[0].name} » ?`}
          description="La suppression est définitive : ces éléments ne passent pas par une corbeille."
          confirmLabel="Supprimer définitivement"
          icon={<Trash2 size={22} />}
          busy={deleting}
          onConfirm={() => void confirmDelete()}
          onClose={() => setPendingDelete(undefined)}
        />
      )}
    </div>
  )
}
