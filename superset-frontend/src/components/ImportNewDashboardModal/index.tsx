/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */
import { FunctionComponent, useState, useCallback } from 'react';
import { useDispatch } from 'react-redux';

import { UploadChangeParam, UploadFile } from 'antd/lib/upload/interface';
import { styled, t } from '@superset-ui/core';

import Button from 'src/components/Button';
import Modal from 'src/components/Modal';
import { Upload } from 'src/components';
import { useImportNewDashboard } from 'src/views/CRUD/hooks';
import { ImportResourceName } from 'src/views/CRUD/types';
import DatabaseSelector from 'src/components/DatabaseSelector';
import { addDangerToast } from 'src/components/MessageToasts/actions';
import ErrorAlert from './ErrorAlert';

// Estilos reutilizados do componente original para manter a consistência visual.
const StyledInputContainer = styled.div`
  padding-bottom: ${({ theme }) => theme.gridUnit * 2}px;
  padding-top: ${({ theme }) => theme.gridUnit * 2}px;

  .control-label {
    display: flex;
    margin-bottom: ${({ theme }) => theme.gridUnit}px;
    font-size: ${({ theme }) => theme.typography.sizes.s}px;
    color: ${({ theme }) => theme.colors.grayscale.dark1};
  }

  input[type='text'] {
    padding: ${({ theme }) => theme.gridUnit * 1.5}px
      ${({ theme }) => theme.gridUnit * 2}px;
    border: 1px solid ${({ theme }) => theme.colors.grayscale.light2};
    border-radius: ${({ theme }) => theme.gridUnit}px;
    width: 100%;
  }

  input::placeholder {
    color: ${({ theme }) => theme.colors.grayscale.light1};
  }
`;

export interface ImportNewDashboardModalProps {
  resourceName: ImportResourceName;
  resourceLabel: string;
  addDangerToast: (msg: string) => void;
  addSuccessToast: (msg: string) => void;
  onDashboardImport: () => void;
  show: boolean;
  onHide: () => void;
}

const ImportNewDashboardModal: FunctionComponent<
  ImportNewDashboardModalProps
> = ({ resourceName, resourceLabel, onDashboardImport, show, onHide }) => {
  const [isHidden, setIsHidden] = useState<boolean>(true);
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [isImporting, setIsImporting] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string>();
  const [databaseId, setDatabaseId] = useState<number>();
  const [schema, setSchema] = useState<string>('');

  // Limpa o estado do modal ao fechar.
  const clearModal = () => {
    setFileList([]);
    setIsImporting(false);
    setErrorMessage('');
    setDatabaseId(undefined);
    setSchema('');
  };

  const handleErrorMsg = (msg: string) => {
    setErrorMessage(msg);
    setIsImporting(false);
  };

  const dispatch = useDispatch();

  const handleError = useCallback(
    (message: string) => {
      dispatch(addDangerToast(message));
    },
    [dispatch],
  );

  const { importResource } = useImportNewDashboard(
    resourceName,
    resourceLabel,
    handleErrorMsg,
  );

  const hide = () => {
    setIsHidden(true);
    onHide();
    clearModal();
  };

  const onImport = async () => {
    if (
      !(fileList[0]?.originFileObj instanceof File) ||
      !databaseId ||
      !schema
    ) {
      return;
    }
    setIsImporting(true);
    const formData = new FormData();
    formData.append('formData', fileList[0].originFileObj);
    formData.append('database_id', String(databaseId));
    if (schema) {
      formData.append('schema', schema);
    }

    const result = await importResource(formData);
    setIsImporting(false);
    if (result) {
      clearModal();
      onDashboardImport();
    }
  };

  const changeFile = (info: UploadChangeParam) => {
    setFileList([
      {
        ...info.file,
        status: 'done',
      },
    ]);
  };

  const removeFile = (removedFile: UploadFile) => {
    setFileList(fileList.filter(file => file.uid !== removedFile.uid));
    return true;
  };

  if (isHidden && show) {
    setIsHidden(false);
  }

  return (
    <Modal
      name="table"
      className="import-dashboard-modal"
      disablePrimaryButton={
        fileList.length === 0 || !databaseId || !schema || isImporting
      }
      onHandledPrimaryAction={onImport}
      onHide={hide}
      primaryButtonName={t('Import')}
      primaryButtonType="primary"
      width="750px"
      show={show}
      title={<h4>{t('Import Dashboard')}</h4>}
    >
      <StyledInputContainer>
        <Upload
          name="tableFile"
          id="tableFile"
          data-test="table-file-input"
          accept=".yaml,.json,.yml,.zip,.csv"
          fileList={fileList}
          onChange={changeFile}
          onRemove={removeFile}
          customRequest={() => {}}
          disabled={isImporting}
        >
          <Button loading={isImporting}>{t('Select file')}</Button>
        </Upload>
      </StyledInputContainer>

      {errorMessage && (
        <ErrorAlert errorMessage={errorMessage} showDbInstallInstructions />
      )}

      <StyledInputContainer>
        <DatabaseSelector
          onDbChange={(db: { id: number; database_name: string }) => {
            setDatabaseId(db.id);
            setSchema('');
          }}
          onSchemaChange={(schema: string) => {
            setSchema(schema);
          }}
          handleError={handleError}
          formMode
        />
      </StyledInputContainer>
    </Modal>
  );
};

export default ImportNewDashboardModal;
