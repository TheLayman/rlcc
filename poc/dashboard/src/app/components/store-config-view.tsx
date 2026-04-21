import { type MouseEvent as ReactMouseEvent, useEffect, useMemo, useRef, useState } from 'react';
import {
  Camera,
  Crosshair,
  MapPinned,
  Plus,
  RefreshCw,
  Save,
  Store,
  Trash2,
  Undo2,
  Video,
} from 'lucide-react';
import { toast } from 'sonner';

import { Badge } from '@/app/components/ui/badge';
import { Button } from '@/app/components/ui/button';
import { Card } from '@/app/components/ui/card';
import { Input } from '@/app/components/ui/input';
import { Label } from '@/app/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/app/components/ui/select';
import { Switch } from '@/app/components/ui/switch';
import { BACKEND_BASE, CV_BASE } from '@/lib/runtime-config';

type PolygonKey = 'seller_zone' | 'bill_zone';
type Point = [number, number];

interface StoreEntry {
  cin: string;
  name: string;
  pos_system: string;
  operator?: string;
}

interface PosZoneConfig {
  zone_id: string;
  seller_zone: Point[];
  bill_zone: Point[];
}

interface CameraMapping {
  seller_window_id: string;
  store_id: string;
  pos_terminal_no: string;
  display_pos_label: string;
  camera_id: string;
  rtsp_url: string;
  xprotect_device_id: string;
  multi_pos: boolean;
  enabled: boolean;
  zones: {
    pos_zones: PosZoneConfig[];
  };
}

interface CameraMappingResponse {
  issues?: string[];
  cameras?: CameraMapping[];
}

function normalizeTerminal(value: string) {
  return (value || '').toUpperCase().replace(/\s+/g, '');
}

function buildSellerWindowId(storeId: string, posTerminalNo: string) {
  return `${storeId}_${normalizeTerminal(posTerminalNo)}`;
}

function slugify(value: string) {
  return (value || 'camera')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '') || 'camera';
}

function ensureMappingShape(camera: CameraMapping): CameraMapping {
  const posZones = camera.zones?.pos_zones?.map(zone => ({
    zone_id: zone.zone_id || normalizeTerminal(camera.display_pos_label || camera.pos_terminal_no || `POS${Math.max(1, 1)}`),
    seller_zone: zone.seller_zone || [],
    bill_zone: zone.bill_zone || [],
  })) || [];

  return {
    ...camera,
    display_pos_label: camera.display_pos_label || camera.pos_terminal_no,
    seller_window_id: buildSellerWindowId(camera.store_id, camera.pos_terminal_no || camera.display_pos_label),
    zones: {
      pos_zones: posZones.length > 0 ? posZones : [{
        zone_id: normalizeTerminal(camera.display_pos_label || camera.pos_terminal_no || 'POS1'),
        seller_zone: [],
        bill_zone: [],
      }],
    },
  };
}

function pointsToString(points: Point[]) {
  return points.map(point => point.join(',')).join(' ');
}

function normalizeStore(store: StoreEntry): StoreEntry {
  return {
    cin: (store.cin || '').trim(),
    name: (store.name || '').trim(),
    pos_system: (store.pos_system || '').trim() || 'Posifly-Dino',
    operator: (store.operator || '').trim(),
  };
}

export function StoreConfigView() {
  const imageRef = useRef<HTMLImageElement | null>(null);

  const [stores, setStores] = useState<StoreEntry[]>([]);
  const [mappings, setMappings] = useState<CameraMapping[]>([]);
  const [issues, setIssues] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [selectedCameraId, setSelectedCameraId] = useState('');
  const [selectedZoneIndex, setSelectedZoneIndex] = useState(0);
  const [activePolygon, setActivePolygon] = useState<PolygonKey>('seller_zone');
  const [frameVersion, setFrameVersion] = useState(Date.now());
  const [imageMeta, setImageMeta] = useState({ naturalWidth: 1280, naturalHeight: 720 });
  const [frameError, setFrameError] = useState('');

  const loadConfig = async (preferredCameraId?: string) => {
    setLoading(true);
    try {
      const [storesRes, mappingsRes] = await Promise.all([
        fetch(`${BACKEND_BASE}/api/stores`),
        fetch(`${BACKEND_BASE}/api/camera-mapping`),
      ]);

      const storesPayload = storesRes.ok ? await storesRes.json() : [];
      const mappingsPayload = mappingsRes.ok ? await mappingsRes.json() : {};

      const nextStores = Array.isArray(storesPayload) ? storesPayload : [];
      const nextMappings = (Array.isArray((mappingsPayload as CameraMappingResponse).cameras)
        ? (mappingsPayload as CameraMappingResponse).cameras!
        : [])
        .map(ensureMappingShape);

      setStores(nextStores);
      setMappings(nextMappings);
      setIssues(Array.isArray((mappingsPayload as CameraMappingResponse).issues) ? (mappingsPayload as CameraMappingResponse).issues! : []);

      const nextSelectedId =
        preferredCameraId && nextMappings.some(camera => camera.camera_id === preferredCameraId)
          ? preferredCameraId
          : nextMappings[0]?.camera_id || '';
      setSelectedCameraId(nextSelectedId);
      setSelectedZoneIndex(0);
      setFrameError('');
      setFrameVersion(Date.now());
    } catch (error) {
      console.error('Failed to load camera mapping:', error);
      toast.error('Failed to load store configuration');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadConfig().catch(() => {});
  }, []);

  useEffect(() => {
    if (!selectedCameraId && mappings.length > 0) {
      setSelectedCameraId(mappings[0].camera_id);
      return;
    }
    if (selectedCameraId && !mappings.some(camera => camera.camera_id === selectedCameraId)) {
      setSelectedCameraId(mappings[0]?.camera_id || '');
    }
  }, [mappings, selectedCameraId]);

  const selectedCamera = useMemo(
    () => mappings.find(camera => camera.camera_id === selectedCameraId) || null,
    [mappings, selectedCameraId],
  );

  useEffect(() => {
    if (!selectedCamera) {
      setSelectedZoneIndex(0);
      return;
    }
    const maxIndex = Math.max(selectedCamera.zones.pos_zones.length - 1, 0);
    if (selectedZoneIndex > maxIndex) {
      setSelectedZoneIndex(maxIndex);
    }
  }, [selectedCamera, selectedZoneIndex]);

  useEffect(() => {
    if (!selectedCameraId) return undefined;
    const timer = window.setInterval(() => setFrameVersion(Date.now()), 2500);
    return () => window.clearInterval(timer);
  }, [selectedCameraId]);

  const selectedZone = selectedCamera?.zones.pos_zones[selectedZoneIndex] || null;
  const selectedStore = stores.find(store => store.cin === selectedCamera?.store_id);
  const frameSrc = selectedCamera
    ? `${CV_BASE}/zones/frame?camera_id=${encodeURIComponent(selectedCamera.camera_id)}&t=${frameVersion}`
    : '';

  const addStore = () => {
    let suffix = stores.length + 1;
    let nextCin = `NEW_STORE_${suffix}`;
    while (stores.some(store => store.cin === nextCin)) {
      suffix += 1;
      nextCin = `NEW_STORE_${suffix}`;
    }
    setStores(prev => [
      ...prev,
      {
        cin: nextCin,
        name: `New Store ${suffix}`,
        pos_system: 'Posifly-Dino',
        operator: '',
      },
    ]);
  };

  const updateStoreField = (index: number, field: keyof StoreEntry, value: string) => {
    const previousCin = stores[index]?.cin || '';
    setStores(prev => prev.map((store, storeIndex) => (
      storeIndex === index ? normalizeStore({ ...store, [field]: value }) : store
    )));

    if (field === 'cin' && previousCin && previousCin !== value) {
      setMappings(prev => prev.map(camera => (
        camera.store_id === previousCin
          ? ensureMappingShape({ ...camera, store_id: value })
          : camera
      )));
    }
  };

  const removeStore = (index: number) => {
    if (stores.length <= 1) {
      toast.error('Keep at least one store in the configuration');
      return;
    }

    const store = stores[index];
    if (!store) return;

    const inUse = mappings.some(camera => camera.store_id === store.cin);
    if (inUse) {
      toast.error('Reassign camera mappings before removing this store');
      return;
    }

    setStores(prev => prev.filter((_, storeIndex) => storeIndex !== index));
  };

  const updateSelectedCamera = (updater: (camera: CameraMapping) => CameraMapping) => {
    if (!selectedCamera) return;
    const currentId = selectedCamera.camera_id;
    setMappings(prev =>
      prev.map(camera => (camera.camera_id === currentId ? ensureMappingShape(updater(camera)) : camera)),
    );
  };

  const updateCameraField = (field: keyof CameraMapping, value: string | boolean) => {
    if (!selectedCamera) return;
    const previousId = selectedCamera.camera_id;
    updateSelectedCamera(camera => ({
      ...camera,
      [field]: value,
    }));
    if (field === 'camera_id' && typeof value === 'string') {
      setSelectedCameraId(value || previousId);
    }
  };

  const updateSelectedZone = (updater: (zone: PosZoneConfig) => PosZoneConfig) => {
    if (!selectedCamera || !selectedZone) return;
    updateSelectedCamera(camera => ({
      ...camera,
      zones: {
        pos_zones: camera.zones.pos_zones.map((zone, index) => (
          index === selectedZoneIndex ? updater(zone) : zone
        )),
      },
    }));
  };

  const addCameraMapping = () => {
    const preferredStore = stores.find(store => store.cin === 'NDCIN1223') || stores[0];
    const storeSlug = slugify(preferredStore?.name || 'camera');
    let suffix = 1;
    let nextCameraId = `cam-${storeSlug}-${String(suffix).padStart(2, '0')}`;
    while (mappings.some(camera => camera.camera_id === nextCameraId)) {
      suffix += 1;
      nextCameraId = `cam-${storeSlug}-${String(suffix).padStart(2, '0')}`;
    }

    const nextMapping = ensureMappingShape({
      seller_window_id: '',
      store_id: preferredStore?.cin || '',
      pos_terminal_no: 'POS 1',
      display_pos_label: 'POS 1',
      camera_id: nextCameraId,
      rtsp_url: '',
      xprotect_device_id: '',
      multi_pos: false,
      enabled: true,
      zones: {
        pos_zones: [
          {
            zone_id: 'POS1',
            seller_zone: [],
            bill_zone: [],
          },
        ],
      },
    });

    setMappings(prev => [...prev, nextMapping]);
    setSelectedCameraId(nextMapping.camera_id);
    setSelectedZoneIndex(0);
    setActivePolygon('seller_zone');
    setFrameVersion(Date.now());
  };

  const removeSelectedCamera = () => {
    if (!selectedCamera) return;
    if (mappings.length <= 1) {
      toast.error('Keep at least one camera mapping in the config');
      return;
    }
    const nextMappings = mappings.filter(camera => camera.camera_id !== selectedCamera.camera_id);
    setMappings(nextMappings);
    setSelectedCameraId(nextMappings[0]?.camera_id || '');
    setSelectedZoneIndex(0);
  };

  const addZone = () => {
    if (!selectedCamera) return;
    const nextIndex = selectedCamera.zones.pos_zones.length + 1;
    const nextZoneId = normalizeTerminal(selectedCamera.display_pos_label || selectedCamera.pos_terminal_no || `POS${nextIndex}`) || `POS${nextIndex}`;
    updateSelectedCamera(camera => ({
      ...camera,
      zones: {
        pos_zones: [
          ...camera.zones.pos_zones,
          {
            zone_id: nextZoneId,
            seller_zone: [],
            bill_zone: [],
          },
        ],
      },
    }));
    setSelectedZoneIndex(selectedCamera.zones.pos_zones.length);
    setActivePolygon('seller_zone');
  };

  const removeZone = () => {
    if (!selectedCamera || !selectedZone) return;
    if (selectedCamera.zones.pos_zones.length <= 1) {
      toast.error('Keep at least one POS zone for each camera');
      return;
    }
    updateSelectedCamera(camera => ({
      ...camera,
      zones: {
        pos_zones: camera.zones.pos_zones.filter((_, index) => index !== selectedZoneIndex),
      },
    }));
    setSelectedZoneIndex(Math.max(0, selectedZoneIndex - 1));
  };

  const addPolygonPoint = (event: ReactMouseEvent<SVGSVGElement>) => {
    if (!selectedZone) return;
    const rect = event.currentTarget.getBoundingClientRect();
    if (!rect.width || !rect.height) return;

    const x = Math.round(((event.clientX - rect.left) / rect.width) * imageMeta.naturalWidth);
    const y = Math.round(((event.clientY - rect.top) / rect.height) * imageMeta.naturalHeight);

    updateSelectedZone(zone => ({
      ...zone,
      [activePolygon]: [...zone[activePolygon], [x, y] as Point],
    }));
  };

  const undoLastPoint = () => {
    if (!selectedZone) return;
    updateSelectedZone(zone => ({
      ...zone,
      [activePolygon]: zone[activePolygon].slice(0, -1),
    }));
  };

  const clearPolygon = () => {
    if (!selectedZone) return;
    updateSelectedZone(zone => ({
      ...zone,
      [activePolygon]: [],
    }));
  };

  const saveConfig = async () => {
    setSaving(true);
    const normalizedStores = stores.map(normalizeStore);
    const blankStore = normalizedStores.find(store => !store.cin || !store.name);
    if (blankStore) {
      toast.error('Every store needs both Store ID and Store Name');
      setSaving(false);
      return;
    }

    const duplicateStoreIds = normalizedStores.map(store => store.cin);
    if (new Set(duplicateStoreIds).size !== duplicateStoreIds.length) {
      toast.error('Store IDs must be unique');
      setSaving(false);
      return;
    }

    const payload = { cameras: mappings.map(ensureMappingShape) };
    try {
      const storesResponse = await fetch(`${BACKEND_BASE}/api/stores`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stores: normalizedStores }),
      });
      const storesResult = await storesResponse.json();
      if (!storesResponse.ok || !storesResult.ok) {
        toast.error(storesResult.message || 'Failed to save stores');
        return;
      }

      const response = await fetch(`${BACKEND_BASE}/api/camera-mapping`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok || !result.ok) {
        toast.error(result.message || 'Failed to save camera mapping');
        return;
      }

      setIssues(Array.isArray(result.issues) ? result.issues : []);
      await loadConfig(selectedCameraId);
      if (result.cv_reloaded) {
        toast.success('Store config saved and CV runtime reloaded');
      } else if (result.cv_reload_error) {
        toast.success('Store config saved');
        toast.error(`CV reload needs attention: ${result.cv_reload_error}`);
      } else {
        toast.success('Store config saved');
      }
    } catch (error) {
      console.error('Failed to save camera mapping:', error);
      toast.error('Failed to save store configuration');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="flex items-center justify-center py-16 text-gray-400">Loading store configuration...</div>;
  }

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-bold text-gray-800">Store Config</h2>
          <p className="text-sm text-gray-500">Assign store IDs, update RTSP URLs, and draw seller and bill zones directly on the live CV frame.</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" className="gap-1.5 h-8 text-xs border-gray-200" onClick={() => loadConfig(selectedCameraId)}>
            <RefreshCw className="h-3 w-3" /> Reload
          </Button>
          <Button size="sm" className="gap-1.5 h-8 text-xs bg-blue-600 hover:bg-blue-700 text-white" onClick={saveConfig} disabled={saving}>
            <Save className="h-3 w-3" /> {saving ? 'Saving...' : 'Save Store Config'}
          </Button>
        </div>
      </div>

      {issues.length > 0 && (
        <Card className="border-amber-200 bg-amber-50 p-4 shadow-sm">
          <div className="text-sm font-semibold text-amber-800">Config Warnings</div>
          <div className="mt-2 space-y-1 text-xs text-amber-700">
            {issues.map(issue => (
              <div key={issue}>{issue}</div>
            ))}
          </div>
        </Card>
      )}

      <Card className="border-gray-200 shadow-sm">
        <div className="border-b border-gray-200 p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-gray-800">Store Directory</div>
              <div className="text-xs text-gray-500">Edit the active POC store catalog. Camera mappings can then reference these store IDs directly.</div>
            </div>
            <Button size="sm" variant="outline" className="h-8 gap-1.5 text-xs border-gray-200" onClick={addStore}>
              <Plus className="h-3 w-3" /> Add Store
            </Button>
          </div>
        </div>
        <div className="space-y-3 p-4">
          {stores.map((store, index) => (
            <div key={`${store.cin || 'store'}-${index}`} className="rounded-xl border border-gray-200 bg-gray-50 p-4">
              <div className="flex items-center justify-between gap-3">
                <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">Store {index + 1}</div>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-8 gap-1.5 border-red-200 text-xs text-red-700 hover:bg-red-50"
                  onClick={() => removeStore(index)}
                >
                  <Trash2 className="h-3 w-3" /> Remove
                </Button>
              </div>
              <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2 2xl:grid-cols-4">
                <div className="space-y-2">
                  <Label htmlFor={`store-cin-${index}`}>Store ID</Label>
                  <Input
                    id={`store-cin-${index}`}
                    value={store.cin}
                    onChange={event => updateStoreField(index, 'cin', event.target.value)}
                    className="border-gray-200 bg-white"
                    placeholder="NDCIN1231"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor={`store-name-${index}`}>Store Name</Label>
                  <Input
                    id={`store-name-${index}`}
                    value={store.name}
                    onChange={event => updateStoreField(index, 'name', event.target.value)}
                    className="border-gray-200 bg-white"
                    placeholder="Nizami Daawat"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor={`store-pos-${index}`}>POS Type</Label>
                  <Input
                    id={`store-pos-${index}`}
                    value={store.pos_system}
                    onChange={event => updateStoreField(index, 'pos_system', event.target.value)}
                    className="border-gray-200 bg-white"
                    placeholder="Posifly-Dino"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor={`store-operator-${index}`}>Operator</Label>
                  <Input
                    id={`store-operator-${index}`}
                    value={store.operator || ''}
                    onChange={event => updateStoreField(index, 'operator', event.target.value)}
                    className="border-gray-200 bg-white"
                    placeholder="Concessionaire / operator"
                  />
                </div>
              </div>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[320px_minmax(0,1fr)]">
        <Card className="border-gray-200 shadow-sm">
          <div className="border-b border-gray-200 p-4">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-semibold text-gray-800">Mapped Cameras</div>
                <div className="text-xs text-gray-500">{mappings.length} camera entries</div>
              </div>
              <Button size="sm" variant="outline" className="h-8 gap-1.5 text-xs border-gray-200" onClick={addCameraMapping}>
                <Plus className="h-3 w-3" /> Add Camera
              </Button>
            </div>
          </div>
          <div className="max-h-[880px] space-y-3 overflow-auto p-4">
            {mappings.map(camera => {
              const store = stores.find(entry => entry.cin === camera.store_id);
              const active = camera.camera_id === selectedCameraId;
              return (
                <button
                  key={camera.camera_id}
                  type="button"
                  onClick={() => {
                    setSelectedCameraId(camera.camera_id);
                    setSelectedZoneIndex(0);
                    setFrameError('');
                    setFrameVersion(Date.now());
                  }}
                  className={`w-full rounded-xl border p-3 text-left transition-all ${
                    active ? 'border-blue-300 bg-blue-50 ring-1 ring-blue-200' : 'border-gray-200 bg-white hover:border-gray-300'
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold text-gray-800">{store?.name || camera.store_id || 'Unassigned store'}</div>
                      <div className="mt-1 truncate text-xs text-gray-500">{camera.camera_id}</div>
                    </div>
                    <Badge className={camera.enabled ? 'border-green-200 bg-green-50 text-green-700' : 'border-gray-200 bg-gray-100 text-gray-500'}>
                      {camera.enabled ? 'Enabled' : 'Disabled'}
                    </Badge>
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] text-gray-500">
                    <div>Store ID</div>
                    <div className="truncate text-right text-gray-700">{camera.store_id || '—'}</div>
                    <div>POS</div>
                    <div className="truncate text-right text-gray-700">{camera.display_pos_label || camera.pos_terminal_no || '—'}</div>
                    <div>RTSP</div>
                    <div className="truncate text-right text-gray-700">{camera.rtsp_url ? 'Configured' : 'Missing'}</div>
                  </div>
                </button>
              );
            })}
          </div>
        </Card>

        {selectedCamera ? (
          <div className="space-y-5">
            <Card className="border-gray-200 shadow-sm">
              <div className="border-b border-gray-200 p-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-sm font-semibold text-gray-800">Camera Mapping</div>
                    <div className="text-xs text-gray-500">Store identity, POS label, and stream details for the selected camera.</div>
                  </div>
                  <Button variant="outline" size="sm" className="h-8 gap-1.5 text-xs border-red-200 text-red-700 hover:bg-red-50" onClick={removeSelectedCamera}>
                    <Trash2 className="h-3 w-3" /> Remove Camera
                  </Button>
                </div>
              </div>
              <div className="grid grid-cols-1 gap-4 p-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="store-id">
                    <Store className="h-3.5 w-3.5 text-blue-600" />
                    Store ID
                  </Label>
                  <Input
                    id="store-id"
                    list="known-store-ids"
                    value={selectedCamera.store_id}
                    onChange={event => updateCameraField('store_id', event.target.value)}
                    className="border-gray-200 bg-white"
                    placeholder="NDCIN1223"
                  />
                  <datalist id="known-store-ids">
                    {stores.map(store => (
                      <option key={store.cin} value={store.cin}>
                        {store.name}
                      </option>
                    ))}
                  </datalist>
                  <div className="text-xs text-gray-500">
                    {selectedStore ? `${selectedStore.name} • ${selectedStore.pos_system}` : 'Enter the CIN / storeIdentifier used by POS and Nukkad'}
                  </div>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="camera-id">
                    <Camera className="h-3.5 w-3.5 text-blue-600" />
                    Camera ID
                  </Label>
                  <Input
                    id="camera-id"
                    value={selectedCamera.camera_id}
                    onChange={event => updateCameraField('camera_id', event.target.value)}
                    className="border-gray-200 bg-white"
                    placeholder="cam-rambandi-01"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="pos-terminal">POS Terminal No</Label>
                  <Input
                    id="pos-terminal"
                    value={selectedCamera.pos_terminal_no}
                    onChange={event => updateSelectedCamera(camera => ({ ...camera, pos_terminal_no: event.target.value }))}
                    className="border-gray-200 bg-white"
                    placeholder="POS 1"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="display-pos">Display POS Label</Label>
                  <Input
                    id="display-pos"
                    value={selectedCamera.display_pos_label}
                    onChange={event => updateSelectedCamera(camera => ({ ...camera, display_pos_label: event.target.value }))}
                    className="border-gray-200 bg-white"
                    placeholder="POS 1"
                  />
                </div>
                <div className="space-y-2 md:col-span-2">
                  <Label htmlFor="rtsp-url">
                    <Video className="h-3.5 w-3.5 text-blue-600" />
                    RTSP URL
                  </Label>
                  <Input
                    id="rtsp-url"
                    value={selectedCamera.rtsp_url}
                    onChange={event => updateCameraField('rtsp_url', event.target.value)}
                    className="border-gray-200 bg-white font-mono text-xs"
                    placeholder="rtsp://user:pass@camera-ip/path"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="xprotect-id">XProtect Device ID</Label>
                  <Input
                    id="xprotect-id"
                    value={selectedCamera.xprotect_device_id}
                    onChange={event => updateCameraField('xprotect_device_id', event.target.value)}
                    className="border-gray-200 bg-white"
                    placeholder="optional"
                  />
                </div>
                <div className="space-y-2">
                  <Label>Seller Window ID</Label>
                  <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs font-mono text-gray-700">
                    {buildSellerWindowId(selectedCamera.store_id, selectedCamera.pos_terminal_no || selectedCamera.display_pos_label)}
                  </div>
                </div>
                <div className="flex items-center justify-between rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                  <div>
                    <div className="text-sm font-medium text-gray-800">Enabled</div>
                    <div className="text-xs text-gray-500">Participates in live CV and mapping checks</div>
                  </div>
                  <Switch checked={selectedCamera.enabled} onCheckedChange={checked => updateCameraField('enabled', checked)} />
                </div>
                <div className="flex items-center justify-between rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                  <div>
                    <div className="text-sm font-medium text-gray-800">Multi-POS Camera</div>
                    <div className="text-xs text-gray-500">Use reduced-confidence correlation for shared views</div>
                  </div>
                  <Switch checked={selectedCamera.multi_pos} onCheckedChange={checked => updateCameraField('multi_pos', checked)} />
                </div>
              </div>
            </Card>

            <div className="grid grid-cols-1 gap-5 2xl:grid-cols-[320px_minmax(0,1fr)]">
              <Card className="border-gray-200 shadow-sm">
                <div className="border-b border-gray-200 p-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="text-sm font-semibold text-gray-800">POS Zones</div>
                      <div className="text-xs text-gray-500">Choose a zone, then click on the frame to place polygon points.</div>
                    </div>
                    <Button size="sm" variant="outline" className="h-8 gap-1.5 text-xs border-gray-200" onClick={addZone}>
                      <Plus className="h-3 w-3" /> Add Zone
                    </Button>
                  </div>
                </div>
                <div className="space-y-4 p-4">
                  <div className="space-y-2">
                    <Label>Active Zone</Label>
                    <Select value={String(selectedZoneIndex)} onValueChange={value => setSelectedZoneIndex(Number(value))}>
                      <SelectTrigger className="border-gray-200 bg-white">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {selectedCamera.zones.pos_zones.map((zone, index) => (
                          <SelectItem key={`${zone.zone_id}-${index}`} value={String(index)}>
                            {zone.zone_id || `Zone ${index + 1}`}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  {selectedZone && (
                    <>
                      <div className="space-y-2">
                        <Label htmlFor="zone-id">
                          <MapPinned className="h-3.5 w-3.5 text-blue-600" />
                          Zone ID
                        </Label>
                        <Input
                          id="zone-id"
                          value={selectedZone.zone_id}
                          onChange={event => updateSelectedZone(zone => ({ ...zone, zone_id: event.target.value }))}
                          className="border-gray-200 bg-white"
                          placeholder="POS1"
                        />
                      </div>

                      <div className="space-y-2">
                        <Label>Polygon Mode</Label>
                        <div className="flex gap-2">
                          <Button
                            type="button"
                            size="sm"
                            variant={activePolygon === 'seller_zone' ? 'default' : 'outline'}
                            className={activePolygon === 'seller_zone' ? 'bg-green-600 text-white hover:bg-green-700' : 'border-gray-200 text-green-700'}
                            onClick={() => setActivePolygon('seller_zone')}
                          >
                            Seller Zone
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            variant={activePolygon === 'bill_zone' ? 'default' : 'outline'}
                            className={activePolygon === 'bill_zone' ? 'bg-amber-500 text-white hover:bg-amber-600' : 'border-gray-200 text-amber-700'}
                            onClick={() => setActivePolygon('bill_zone')}
                          >
                            Bill Zone
                          </Button>
                        </div>
                      </div>

                      <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 text-xs text-gray-600">
                        <div>Selected polygon: <span className="font-semibold text-gray-800">{activePolygon === 'seller_zone' ? 'Seller' : 'Bill'}</span></div>
                        <div className="mt-1">Points: {selectedZone[activePolygon].length}</div>
                      </div>

                      <div className="grid grid-cols-2 gap-2">
                        <Button type="button" size="sm" variant="outline" className="h-8 gap-1.5 border-gray-200 text-xs" onClick={undoLastPoint}>
                          <Undo2 className="h-3 w-3" /> Undo Point
                        </Button>
                        <Button type="button" size="sm" variant="outline" className="h-8 gap-1.5 border-red-200 text-xs text-red-700 hover:bg-red-50" onClick={clearPolygon}>
                          <Trash2 className="h-3 w-3" /> Clear Polygon
                        </Button>
                        <Button type="button" size="sm" variant="outline" className="col-span-2 h-8 gap-1.5 border-red-200 text-xs text-red-700 hover:bg-red-50" onClick={removeZone}>
                          <Trash2 className="h-3 w-3" /> Remove Zone
                        </Button>
                      </div>
                    </>
                  )}
                </div>
              </Card>

              <Card className="border-gray-200 shadow-sm">
                <div className="border-b border-gray-200 p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-semibold text-gray-800">Live Frame Zone Editor</div>
                      <div className="text-xs text-gray-500">Click on the frame to append points to the active polygon. Save to persist and reload CV.</div>
                    </div>
                    <Button type="button" size="sm" variant="outline" className="h-8 gap-1.5 text-xs border-gray-200" onClick={() => setFrameVersion(Date.now())}>
                      <RefreshCw className="h-3 w-3" /> Refresh Frame
                    </Button>
                  </div>
                </div>
                <div className="space-y-4 p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge className="border-blue-200 bg-blue-50 text-blue-700">{selectedCamera.camera_id}</Badge>
                    <Badge className="border-gray-200 bg-gray-100 text-gray-700">{selectedCamera.display_pos_label || selectedCamera.pos_terminal_no}</Badge>
                    <Badge className={selectedCamera.rtsp_url ? 'border-green-200 bg-green-50 text-green-700' : 'border-amber-200 bg-amber-50 text-amber-700'}>
                      {selectedCamera.rtsp_url ? 'RTSP Ready' : 'RTSP Missing'}
                    </Badge>
                    <Badge className={frameError ? 'border-red-200 bg-red-50 text-red-700' : 'border-gray-200 bg-gray-100 text-gray-700'}>
                      {frameError ? 'Frame Pending' : 'Frame Active'}
                    </Badge>
                  </div>

                  <div className="rounded-lg border border-gray-200 bg-slate-950 p-3">
                    <div className="relative overflow-hidden rounded-lg">
                      {frameSrc ? (
                        <>
                          <img
                            ref={imageRef}
                            src={frameSrc}
                            alt="Live camera frame"
                            className="block w-full rounded-lg"
                            onLoad={event => {
                              setFrameError('');
                              setImageMeta({
                                naturalWidth: event.currentTarget.naturalWidth || 1280,
                                naturalHeight: event.currentTarget.naturalHeight || 720,
                              });
                            }}
                            onError={() => {
                              setFrameError('Frame not available yet. Save the mapping and confirm the RTSP stream.');
                            }}
                          />
                          <svg
                            viewBox={`0 0 ${imageMeta.naturalWidth} ${imageMeta.naturalHeight}`}
                            preserveAspectRatio="none"
                            className="absolute inset-0 h-full w-full cursor-crosshair"
                            onClick={addPolygonPoint}
                          >
                            {selectedCamera.zones.pos_zones.map((zone, index) => {
                              const isSelected = index === selectedZoneIndex;
                              const sellerActive = isSelected && activePolygon === 'seller_zone';
                              const billActive = isSelected && activePolygon === 'bill_zone';
                              const labelPoint = zone.seller_zone[0] || zone.bill_zone[0];

                              return (
                                <g key={`${zone.zone_id}-${index}`}>
                                  {zone.seller_zone.length > 0 && (
                                    <polygon
                                      points={pointsToString(zone.seller_zone)}
                                      fill={sellerActive ? 'rgba(34, 197, 94, 0.26)' : 'rgba(34, 197, 94, 0.14)'}
                                      stroke={sellerActive ? '#16a34a' : '#22c55e'}
                                      strokeWidth={sellerActive ? 4 : 2}
                                    />
                                  )}
                                  {zone.bill_zone.length > 0 && (
                                    <polygon
                                      points={pointsToString(zone.bill_zone)}
                                      fill={billActive ? 'rgba(245, 158, 11, 0.30)' : 'rgba(251, 191, 36, 0.16)'}
                                      stroke={billActive ? '#d97706' : '#f59e0b'}
                                      strokeWidth={billActive ? 4 : 2}
                                    />
                                  )}
                                  {labelPoint && (
                                    <text
                                      x={labelPoint[0] + 10}
                                      y={labelPoint[1] - 8}
                                      fill="#ffffff"
                                      fontSize="20"
                                      fontWeight="700"
                                      stroke="#0f172a"
                                      strokeWidth="0.8"
                                    >
                                      {zone.zone_id}
                                    </text>
                                  )}
                                  {isSelected && zone[activePolygon].map((point, pointIndex) => (
                                    <g key={`${zone.zone_id}-${activePolygon}-${pointIndex}`}>
                                      <circle cx={point[0]} cy={point[1]} r={7} fill="#ffffff" stroke="#2563eb" strokeWidth={3} />
                                      <text x={point[0] + 10} y={point[1] + 6} fill="#ffffff" fontSize="18" fontWeight="700">
                                        {pointIndex + 1}
                                      </text>
                                    </g>
                                  ))}
                                </g>
                              );
                            })}
                          </svg>
                        </>
                      ) : (
                        <div className="flex h-[420px] items-center justify-center rounded-lg bg-slate-900 text-sm text-slate-400">
                          Select a camera mapping to start
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                    <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                      <div className="flex items-center gap-2 text-sm font-medium text-gray-800">
                        <Crosshair className="h-4 w-4 text-blue-600" />
                        Drawing Tips
                      </div>
                      <div className="mt-2 space-y-1 text-xs text-gray-600">
                        <div>1. Choose the POS zone and polygon type.</div>
                        <div>2. Click the live frame to place each polygon vertex.</div>
                        <div>3. Use “Undo Point” or “Clear Polygon” if you miss a point.</div>
                        <div>4. Save the config to persist the zones and reload CV.</div>
                      </div>
                    </div>
                    <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                      <div className="text-sm font-medium text-gray-800">Active Polygon Points</div>
                      <div className="mt-2 space-y-1 font-mono text-xs text-gray-600">
                        {selectedZone && selectedZone[activePolygon].length > 0 ? (
                          selectedZone[activePolygon].map((point, index) => (
                            <div key={`${activePolygon}-${index}`}>{index + 1}. [{point[0]}, {point[1]}]</div>
                          ))
                        ) : (
                          <div>No points yet. Click on the frame to start drawing.</div>
                        )}
                      </div>
                    </div>
                  </div>

                  {frameError && (
                    <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                      {frameError}
                    </div>
                  )}
                </div>
              </Card>
            </div>
          </div>
        ) : (
          <Card className="border-gray-200 p-10 text-center text-sm text-gray-500 shadow-sm">
            No camera mappings yet. Add one to start configuring stores and drawing zones.
          </Card>
        )}
      </div>
    </div>
  );
}
