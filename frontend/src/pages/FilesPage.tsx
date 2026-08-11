import {
  Check,
  ChevronRight,
  Download,
  File,
  FileArchive,
  FileImage,
  FileText,
  Folder,
  FolderPlus,
  MoreVertical,
  Search,
  Trash2,
  UploadCloud,
  X,
} from 'lucide-react'
import { ChangeEvent, DragEvent, FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, uploadFile } from '../api'
import { ActivityPanel } from '../components/ActivityPanel'
import { ChatPanel } from '../components/ChatPanel'
import { formatBytes, relativeTime } from '../lib'
import type { Activity, FilesPayload, SharedFile } from '../types'

type Props = {
  activities: Activity[]
  chat: {
    messages: import('../types').ChatMessage[]
    online: number
    connected: boolean
    send: (body: string) => boolean
  }
}

function FileIcon({ item }: { item: SharedFile }) {
  if (item.is_directory) return <Folder className="file-folder" />
  if (item.mime.startsWith('image/')) return <FileImage className="file-image" />
  if (item.mime.includes('zip')) return <FileArchive className="file-archive" />
  if (item.mime.includes('pdf') || item.mime.startsWith('text/')) return <FileText className="file-document" />
  return <File />
}

export function FilesPage({ activities, chat }: Props) {
  const [currentPath, setCurrentPath] = useState('')
  const [payload, setPayload] = useState<FilesPayload>()
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [upload, setUpload] = useState<{ name: string; progress: number }>()
  const [dragging, setDragging] = useState(false)
  const [folderDialog, setFolderDialog] = useState(false)
  const [folderName, setFolderName] = useState('')
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
  useEffect(() => { void refresh() }, [refresh])

  const visible = useMemo(() => {
    const query = search.trim().toLocaleLowerCase('fr')
    return payload?.items.filter((item) => item.name.toLocaleLowerCase('fr').includes(query)) ?? []
  }, [payload, search])

  const processFile = async (file: globalThis.File) => {
    setUpload({ name: file.name, progress: 0 })
    setError('')
    try {
      await uploadFile(file, currentPath, (progress) => setUpload({ name: file.name, progress }))
      await refresh()
      window.setTimeout(() => setUpload(undefined), 600)
    } catch (reason) {
      setUpload(undefined)
      setError(reason instanceof Error ? reason.message : 'Échec de l’importation')
    }
  }

  const fileChosen = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (file) void processFile(file)
    event.target.value = ''
  }
  const drop = (event: DragEvent) => {
    event.preventDefault()
    setDragging(false)
    const file = event.dataTransfer.files[0]
    if (file) void processFile(file)
  }
  const createFolder = async (event: FormEvent) => {
    event.preventDefault()
    try {
      await api.createFolder(currentPath, folderName)
      setFolderDialog(false)
      setFolderName('')
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Création impossible')
    }
  }
  const remove = async (item: SharedFile) => {
    if (!window.confirm(`Supprimer « ${item.name} » ?`)) return
    try {
      await api.deleteFile(item.path)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Suppression impossible')
    }
  }
  const toggle = (path: string) => setSelected((current) => {
    const next = new Set(current)
    next.has(path) ? next.delete(path) : next.add(path)
    return next
  })
  const used = payload?.storage.used ?? 0
  const percent = payload ? Math.round((used / payload.storage.total) * 100) : 0
  const breadcrumbs = currentPath ? currentPath.split('/') : []

  return (
    <div className="page files-page">
      <div className="files-layout">
        <main className="files-main">
          <div className="page-title title-with-action">
            <div><h1>Fichiers partagés</h1><p>Un espace privé, uniquement disponible sur votre réseau local.</p></div>
            <div className="title-actions">
              <button className="button button-secondary" onClick={() => setFolderDialog(true)}><FolderPlus size={17} />Nouveau dossier</button>
              <button className="button button-outline" onClick={() => inputRef.current?.click()}><UploadCloud size={17} />Importer des fichiers</button>
              <input ref={inputRef} className="sr-only" type="file" onChange={fileChosen} />
            </div>
          </div>
          <div className="breadcrumbs">
            <button onClick={() => setCurrentPath('')}>Accueil</button>
            {breadcrumbs.map((part, index) => (
              <span key={`${part}-${index}`}><ChevronRight size={14} /><button onClick={() => setCurrentPath(breadcrumbs.slice(0, index + 1).join('/'))}>{part}</button></span>
            ))}
          </div>
          {error && <div className="inline-error"><X size={17} />{error}<button onClick={() => setError('')} aria-label="Fermer"><X size={15} /></button></div>}
          <section className="panel storage-panel">
            <strong>{formatBytes(used)} <span>utilisés sur {formatBytes(payload?.storage.total ?? 0)}</span></strong>
            <div className="storage-track"><span style={{ width: `${percent}%` }} /></div>
            <small>{percent}% utilisés</small>
          </section>
          <section className="panel file-browser">
            <div className="file-toolbar">
              <label className="select-box"><input type="checkbox" checked={visible.length > 0 && selected.size === visible.length} onChange={() => setSelected(selected.size === visible.length ? new Set() : new Set(visible.map((item) => item.path)))} /><span><Check size={13} /></span></label>
              <label className="search-field"><Search size={17} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Rechercher dans les fichiers" /></label>
            </div>
            <div
              className={`drop-zone ${dragging ? 'drop-zone-active' : ''}`}
              onDragOver={(event) => { event.preventDefault(); setDragging(true) }}
              onDragLeave={() => setDragging(false)}
              onDrop={drop}
              onClick={() => inputRef.current?.click()}
            >
              <UploadCloud size={28} /><span>Glissez-déposez un fichier ici pour l’importer</span>
            </div>
            {upload && (
              <div className="upload-row">
                <File size={19} /><strong>{upload.name}</strong><div className="upload-track"><span style={{ width: `${upload.progress}%` }} /></div><span>{upload.progress}%</span>
              </div>
            )}
            <div className="file-table">
              <div className="file-row file-head"><span /><span>Nom</span><span>Type</span><span>Taille</span><span>Modifié</span><span>Actions</span></div>
              {visible.map((item) => (
                <div className={`file-row ${selected.has(item.path) ? 'file-selected' : ''}`} key={item.path}>
                  <label className="select-box"><input type="checkbox" checked={selected.has(item.path)} onChange={() => toggle(item.path)} /><span><Check size={13} /></span></label>
                  <button className="file-name" onClick={() => item.is_directory ? setCurrentPath(item.path) : undefined}><FileIcon item={item} /><strong>{item.name}</strong></button>
                  <span>{item.is_directory ? 'Dossier' : item.mime.split('/')[1]?.toUpperCase() || 'Fichier'}</span>
                  <span>{item.is_directory ? '—' : formatBytes(item.size)}</span>
                  <span>{relativeTime(item.modified_at)}</span>
                  <span className="file-actions">
                    {!item.is_directory && <a href={`/api/v1/files/download?path=${encodeURIComponent(item.path)}`} className="icon-button" title="Télécharger"><Download size={16} /></a>}
                    <button className="icon-button danger-hover" onClick={() => void remove(item)} title="Supprimer"><Trash2 size={16} /></button>
                    <button className="icon-button" title="Plus d’actions"><MoreVertical size={17} /></button>
                  </span>
                </div>
              ))}
              {!visible.length && <div className="empty-row">Ce dossier est vide.</div>}
              <footer>{visible.length} élément{visible.length !== 1 ? 's' : ''}<span>{selected.size} sélectionné{selected.size !== 1 ? 's' : ''}</span></footer>
            </div>
          </section>
        </main>
        <aside className="files-rail"><ActivityPanel activities={activities.slice(0, 4)} /><ChatPanel {...chat} compact /></aside>
      </div>
      {folderDialog && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setFolderDialog(false)}>
          <form className="modal" onSubmit={createFolder} onMouseDown={(event) => event.stopPropagation()}>
            <button type="button" className="icon-button modal-close" onClick={() => setFolderDialog(false)} aria-label="Fermer"><X size={18} /></button>
            <span className="modal-icon"><FolderPlus size={22} /></span>
            <h2>Nouveau dossier</h2><p>Il sera créé dans {currentPath || 'Accueil'}.</p>
            <label>Nom du dossier<input value={folderName} onChange={(event) => setFolderName(event.target.value)} autoFocus required maxLength={120} /></label>
            <div className="modal-actions"><button type="button" className="button button-secondary" onClick={() => setFolderDialog(false)}>Annuler</button><button className="button button-primary">Créer</button></div>
          </form>
        </div>
      )}
    </div>
  )
}
