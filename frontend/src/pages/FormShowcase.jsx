import React, { useState } from 'react';
import { FormStandardProvider, FormSection, FormField, FormProgress, SectionChecklist } from '../components/form-standard';
import { useFormStandard } from '../components/form-standard/FormContext';
import { Input } from '../components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Textarea } from '../components/ui/textarea';
import { Button } from '../components/ui/button';

// Helper component to trigger scroll
const SubmitButton = () => {
  const { scrollToFirstInvalid } = useFormStandard();
  return (
    <Button
      onClick={() => {
        const scrolled = scrollToFirstInvalid();
        if (!scrolled) {
          alert("Semua validasi berhasil!");
        }
      }}
      className="w-full sm:w-auto"
    >
      Simpan Data
    </Button>
  );
};

export default function FormShowcase() {
  const [formData, setFormData] = useState({
    nama: 'John Doe',
    nik: '3201234567890001',
    posisi: 'Operator Mesin',
    bank: 'BCA',
    rekening: '', // Incomplete
    npwp: '12.345.678.9-012.000',
    ktpPath: '' // Error simulated by passing error prop manually
  });

  const sections = [
    { id: 'data_pribadi', title: 'Data Pribadi' },
    { id: 'data_kepegawaian', title: 'Data Kepegawaian' },
    { id: 'rekening', title: 'Rekening & Pajak' },
    { id: 'dokumen', title: 'Dokumen Pendukung' }
  ];

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-900">Form UI Standard Showcase</h1>
        <p className="text-gray-500 mt-1">Development only preview untuk standard UI baru.</p>
      </div>

      <FormStandardProvider defaultValues={formData}>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 space-y-6">
            {/* A. Data Pribadi (Complete) */}
            <FormSection id="data_pribadi" title="A. Data Pribadi" description="Informasi dasar karyawan.">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <FormField id="nama" label="Nama Lengkap" isRequired>
                  <Input placeholder="Masukkan nama lengkap" defaultValue={formData.nama} />
                </FormField>
                <FormField id="nik" label="NIK" isRequired>
                  <Input placeholder="16 digit NIK" defaultValue={formData.nik} />
                </FormField>
              </div>
              <FormField id="alamat" label="Alamat Domisili" helpText="Alamat tempat tinggal saat ini (opsional)">
                <Textarea placeholder="Masukkan alamat lengkap..." />
              </FormField>
            </FormSection>

            {/* B. Data Kepegawaian (75% lengkap) */}
            <FormSection id="data_kepegawaian" title="B. Data Kepegawaian" description="Detail posisi dan status kerja.">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <FormField id="posisi" label="Posisi" isRequired>
                  <Input defaultValue={formData.posisi} readOnly disabled />
                </FormField>
                <FormField id="status_karyawan" label="Status Karyawan" isRequired>
                  <Select>
                    <SelectTrigger>
                      <SelectValue placeholder="Pilih status" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="tetap">Karyawan Tetap</SelectItem>
                      <SelectItem value="kontrak">Kontrak</SelectItem>
                      <SelectItem value="harian">Harian Lepas</SelectItem>
                    </SelectContent>
                  </Select>
                </FormField>
              </div>
              <FormField id="tanggal_masuk" label="Tanggal Masuk" isRequired>
                <Input type="date" />
              </FormField>
            </FormSection>

            {/* C. Rekening (Incomplete/Required) */}
            <FormSection id="rekening" title="C. Rekening & Pajak" description="Informasi pembayaran gaji.">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <FormField id="bank" label="Nama Bank" isRequired>
                  <Select defaultValue="bca">
                    <SelectTrigger>
                      <SelectValue placeholder="Pilih bank" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="bca">BCA</SelectItem>
                      <SelectItem value="mandiri">Mandiri</SelectItem>
                      <SelectItem value="bni">BNI</SelectItem>
                    </SelectContent>
                  </Select>
                </FormField>
                <FormField id="rekening" label="Nomor Rekening" isRequired>
                  <Input placeholder="Nomor rekening" defaultValue={formData.rekening} />
                </FormField>
              </div>
              <FormField id="npwp" label="NPWP">
                <Input placeholder="Nomor NPWP" defaultValue={formData.npwp} />
              </FormField>
            </FormSection>

            {/* D. Dokumen (Error) */}
            <FormSection id="dokumen" title="D. Dokumen Pendukung" description="Upload file pendukung.">
              <FormField id="ktpPath" label="Scan KTP" isRequired error="File KTP wajib diunggah dalam format JPG/PNG max 2MB.">
                <Input type="file" accept="image/*" />
              </FormField>
              <FormField id="kkPath" label="Scan Kartu Keluarga" disabled helpText="Upload dinonaktifkan sementara.">
                <Input type="file" accept="image/*" disabled />
              </FormField>
            </FormSection>

            <div className="flex justify-end pt-4 border-t">
              <SubmitButton />
            </div>

          </div>

          {/* Sidebar */}
          <div className="space-y-6">
            <div className="sticky top-6 space-y-6">
              <FormProgress />
              <SectionChecklist sections={sections} />
            </div>
          </div>
        </div>
      </FormStandardProvider>
    </div>
  );
}
